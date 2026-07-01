"""Finnish corrigendum (oikaisu) parser and patch table.

A corrigendum is a legally binding correction to a published statute — in LawVM's
model, it is an amendment whose parent is another amendment. Corrections are
represented as `LegalOperation(action="text_replace")`, now with a typed
`text_patch` carrier as the authoritative payload.

PUBLIC API
----------
extract_inline_corrections(xml_bytes, statute_id) -> (List[LegalOperation], bytes)
    Population A: extract ops from <span class="corrigendum"><authorialNote>
    in corpus XML. Returns (ops, cleaned_bytes) where cleaned_bytes
    has all authorialNote elements stripped from corrigendum spans. Safe to call
    on any AKN XML — returns ([], xml_bytes) if no corrigendum spans present.

parse_corrigendum(pdf_text, amendment_id) -> ParsedCorrigendumResult
    Population B: regex-first parser for the standardized Finnish corrigendum
    format. Each "on:\\n[WRONG]\\nPitää olla:\\n[CORRECT]" pair becomes one op.
    Unsupported ADD/puuttuu blocks remain visible in unsupported_patches.

CorrigendumPatchTable
    Loads all classified corrigenda from the git-tracked text corpus.
    Provides patch_source_xml(xml_bytes, amendment_mid) for use in process_muutoslaki.

INTEGRATION
-----------
Population A (inline XML): call extract_inline_corrections() when loading any
statute XML (base or amendment). The cleaned bytes suppress authorialNote leakage
into extracted text. The returned ops record what was corrected.

Population B (PDF-based, amendment johtolause): In grafter.process_muutoslaki(),
after reading amendment XML:

    from lawvm.core.archive_safety import ArchiveMemberTooLarge, safe_zip_read
    # Wave 3 decompression-bomb cap (Security M1): never bare zf.read() a
    # zip member — safe_zip_read checks the declared uncompressed size
    # against $LAWVM_MAX_ARCHIVE_MEMBER_BYTES BEFORE materialising, and
    # ArchiveMemberTooLarge carries (archive_path, member_name,
    # declared_size, cap_bytes) so the skip is visible (AGENTS.md §1.8/§1.10)
    # rather than an OOM. The caller's zip_label/.farchive path is the
    # archive_path receipt field.
    try:
        xml_bytes = safe_zip_read(
            zf,
            f"akn/fi/act/statute/{amendment_id}/fin@/main.xml",
            archive_path=str(zip_label),
        )
    except ArchiveMemberTooLarge as exc:
        # Corrigendum-patch lane is non-blocking: skip this amendment's
        # patch and continue replay (over-retention is the safe wrong per
        # AGENTS.md §0), but the rejection MUST stay visible — emit a typed
        # finding with exc.diagnostic.render_reason() and continue, OR
        # re-raise if the outer grafter already has a typed accumulator.
        raise
    xml_bytes, applied = get_patch_table().patch_source_xml(xml_bytes, amendment_id)

If applied is non-empty, the amendment johtolause (or body text) was corrected
before PEG parsing, fixing ops that targeted wrong provisions.

FORMAT (empirically confirmed, fully regular)
---------------------------------------------
  Oikaisuja Suomen säädöskokoelmaan

  Suomen säädöskokoelmaan n:o NNNN/YYYY
  (Amendment title)
  Sivulla N, LOCATION on:
  WRONG TEXT (one or more lines)
  Pitää olla:
  CORRECT TEXT (one or more lines)

Multiple correction blocks per PDF are supported.
sk* = Finnish (relevant to LawVM), fs* = Swedish (skipped).
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Literal, Optional, Tuple

import Levenshtein
from lxml import etree
import yaml

from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.mutation_boundary import TreePathStep
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.corrigendum_records import (
    _RETRY_OVERLAYS_JSONL,  # noqa: F401 — used by tools.corr_records writers
    _UNRESOLVABLE_YAML,
    default_patch_records_path,
    load_patch_records,
    load_unresolvable_overlay_stable_ids,
)
from lawvm.finland.metadata import _normalize_fi_parse_text as _normalize_ws_base

# ---------------------------------------------------------------------------
# Population A: inline XML corrigenda
# ---------------------------------------------------------------------------
#
# Finlex source XML in the corpus (1990-2001 era, ~116 statutes, ~156 spans)
# already contains the CORRECTED text. The original wrong text is preserved in
# an authorialNote inside the span, for historical record:
#
#   <span class="corrigendum">
#       CORRECTED TEXT
#       <authorialNote marker="1" placement="bottom">
#           <p>Merkitty kohta oikaistu (v. YYYY), alkuperäinen sanamuoto kuului:</p>
#           <p>ORIGINAL WRONG TEXT</p>
#       </authorialNote>
#   </span>
#
# The authorialNote leaks into LawVM's extracted text (it's inside the span).
# extract_inline_corrections() does two things:
#   1. Records each correction as a LegalOperation with typed text_patch as the
#      authoritative payload. Legacy loose text fields are intentionally not
#      duplicated here; downstream compatibility can project them when needed.
#   2. Returns cleaned bytes with all authorialNotes stripped from the XML
#
# All 156 authorialNotes in the corpus XML are inside corrigendum spans — so
# stripping all authorialNotes is safe and equivalent.

_CORR_YEAR_RE = re.compile(r"oikaistu.*?v\.\s*(\d{4})", re.IGNORECASE)
_NUM_STRIP_RE = re.compile(r"[\u2009\xa0\s]+")  # thin space, NBSP, whitespace


@dataclass(frozen=True)
class UnsupportedCorrigendumPatch:
    """Non-executable corrigendum patch that must remain visible to the ledger."""

    amendment_id: str
    sequence: int
    correction_kind: str
    location: str
    target: LegalAddress
    correct_text: str
    reason: str
    source_statute: str
    wrong_text: str = ""


@dataclass(frozen=True)
class ParsedCorrigendumResult:
    """Structured corrigendum parse result with executable and unsupported lanes."""

    ops: tuple[LegalOperation, ...]
    unsupported_patches: tuple[UnsupportedCorrigendumPatch, ...] = ()

    def __iter__(self) -> Iterator[LegalOperation]:
        return iter(self.ops)

    def __len__(self) -> int:
        return len(self.ops)

    def __getitem__(self, index: int) -> LegalOperation:
        return self.ops[index]


@dataclass(frozen=True, slots=True)
class _NearDuplicatePatchCandidate:
    wrong_compact: str
    correct_compact: str


# ---------------------------------------------------------------------------
# Upstream-corrigenda retry overlays (per-``stable_id`` authority layer).
#
# A retry overlay targets one ``stable_id`` in the official classified records
# (``corrigendum_official_fi.jsonl``). The official row there represents what
# the LLM/regex/vision extractors produced — its ``(wrong_text, correct_text)``
# may not byte-match in source XML (LLM ellipsis spans, multi-block collapse,
# truncated body text). The retry overlay carries one or more byte-exact
# ``patches`` that, applied together, realise the same upstream-corrigendum
# effect that the LLM was reaching for.
#
# Authority relationship (AGENTS.md §0/§2.10): the overlay's authority comes
# from the upstream Finlex corrigendum PDF (sha256'd in
# ``corrigendum_sources_fi.jsonl``); the byte patches here are the *execution-
# authorized form* of that corrigendum, not a LawVM-authored repair. That's
# the distinction from source-defect fixes (``_SOURCE_DEFECT_YAML``), whose
# authority comes from a typed source witness because no upstream
# corrigendum exists.
#
# At load time, ``load_from_source`` SNIPS the official row whose
# ``stable_id`` matches an overlay target and EMITS ops only from the overlay
# patches, with the same dedup guards as a regular row. Per-overlay patches
# dedup against earlier-processed rows in the same amendment — they never
# wipe siblings (the legacy per-amendment "REPLACE all DB patches" semantics
# that silently destroyed legitimate corrigendum content in the same
# amendment when any retry landed).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RetryOverlayPatch:
    """One byte-exact ``(wrong_text, correct_text)`` patch in an overlay."""

    wrong_text: str
    correct_text: str
    # Expected exact-occurrence count when this patch is applied to source XML.
    # Default 1 — apply loop emits ``under_applied`` when the byte span appears
    # fewer than this many times and ``ambiguous`` when more (AGENTS.md §1.8).
    expected_apply_count: int = 1


@dataclass(frozen=True, slots=True)
class _RetryOverlay:
    """One overlay targeting a single ``stable_id`` in the official records."""

    stable_id: str
    rule_id: str
    family: str
    amendment_id_numyr: str
    source_pdf_witness: str
    correction_type: str
    span_verified: bool
    verified_at: str
    patches: Tuple[_RetryOverlayPatch, ...]


def _load_retry_overlays(path: Optional[Path] = None) -> "dict[str, _RetryOverlay]":
    """Load retry-overlay records from ``_RETRY_OVERLAYS_JSONL``.

    Returns a dict keyed by overlay-targeted ``stable_id``. Lenient on a
    missing file (returns ``{}``) — the overlay layer is optional, and the
    core loader must produce a coherent patch table with or without it.
    Each record is validated: overlays without a stable_id or with an
    empty patches list are dropped, and failing individual records emit a
    typed ``_record_misapplied`` diagnostic rather than aborting the whole
    load.
    """
    target_path = path or _RETRY_OVERLAYS_JSONL
    if not target_path.exists():
        return {}
    overlays: "dict[str, _RetryOverlay]" = {}
    with target_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                _record_misapplied(
                    op_id=f"corr/retry_overlay/parse/{line_no}",
                    amendment_id="retry_overlay_jsonl",
                    statute_id=str(target_path.name),
                    reason="FINLAND.CORRIGENDUM_RETRY_OVERLAY_PARSE_FAILED",
                    surface="retry_overlay",
                    wrong_text=line[:300],
                    correct_text="",
                    path=str(target_path),
                    line_no=line_no,
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
                continue
            stable_id = str(record.get("stable_id") or "").strip()
            if not stable_id:
                _record_misapplied(
                    op_id=f"corr/retry_overlay/{line_no}/missing_stable_id",
                    amendment_id="retry_overlay_jsonl",
                    statute_id=str(target_path.name),
                    reason="FINLAND.CORRIGENDUM_RETRY_OVERLAY_MISSING_STABLE_ID",
                    surface="retry_overlay",
                    wrong_text=str(record)[:300],
                    correct_text="",
                    line_no=line_no,
                )
                continue
            patches_raw = record.get("patches") or []
            patches_list: List[_RetryOverlayPatch] = []
            for p in patches_raw:
                w = str(p.get("wrong_text") or "").strip()
                c = str(p.get("correct_text") or "").strip()
                if w and c and w != c:
                    try:
                        eac = int(p.get("expected_apply_count") or 1)
                    except (TypeError, ValueError):
                        eac = 1
                    if eac < 1:
                        eac = 1
                    patches_list.append(
                        _RetryOverlayPatch(
                            wrong_text=w, correct_text=c, expected_apply_count=eac
                        )
                    )
            if not patches_list:
                _record_misapplied(
                    op_id=f"corr/retry_overlay/{stable_id}/empty_patches",
                    amendment_id="retry_overlay_jsonl",
                    statute_id=str(target_path.name),
                    reason="FINLAND.CORRIGENDUM_RETRY_OVERLAY_EMPTY_PATCHES",
                    surface="retry_overlay",
                    wrong_text=stable_id,
                    correct_text="",
                )
                continue
            # Duplicate-overlay-target diagnostic: two overlays cannot target
            # the same stable_id (the second would silently drop the first).
            if stable_id in overlays:
                _record_misapplied(
                    op_id=f"corr/retry_overlay/{stable_id}/duplicate_target",
                    amendment_id="retry_overlay_jsonl",
                    statute_id=str(target_path.name),
                    reason="FINLAND.CORRIGENDUM_RETRY_OVERLAY_DUPLICATE_TARGET",
                    surface="retry_overlay",
                    wrong_text=stable_id,
                    correct_text="",
                )
                continue  # keep the first overlay
            overlays[stable_id] = _RetryOverlay(
                stable_id=stable_id,
                rule_id=str(
                    record.get("rule_id") or "FINLAND.CORR.EXTRACTION_RETRY"
                ),
                family=str(record.get("family") or ""),
                amendment_id_numyr=str(record.get("amendment_id") or "").strip(),
                source_pdf_witness=str(record.get("source_pdf_witness") or "").strip(),
                correction_type=str(
                    record.get("correction_type") or "johtolause"
                ).strip()
                or "johtolause",
                span_verified=bool(record.get("span_verified", False)),
                verified_at=str(record.get("verified_at") or "").strip(),
                patches=tuple(patches_list),
            )
    return overlays


def _corrigendum_text_replace_op(
    *,
    op_id: str,
    sequence: int,
    target: LegalAddress,
    wrong_text: str,
    correct_text: str,
    source: OperationSource,
) -> LegalOperation:
    """Build a structured text_replace op for corrigendum patches."""
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        provenance_tags=(wrong_text,),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=wrong_text),
            replacement=correct_text,
        ),
        source=source,
    )


def _extract_num_from_el(el: etree._Element) -> str:
    """Extract plain number string from a section/subsection element."""
    # Try namespaced num first, then bare (explicit is-None check — lxml
    # element truth-testing is deprecated and gives FutureWarning)
    num_el = el.find("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}num")
    if num_el is None:
        num_el = el.find("num")
    if num_el is not None and num_el.text:
        m = re.search(r"(\d+[a-z]?)", _NUM_STRIP_RE.sub(" ", num_el.text))
        return m.group(1) if m else num_el.text.strip()
    eid = el.get("eId", "")
    if eid:
        m = re.search(r"_(\d+[a-z]?)(?:v\d+)?$", eid.split("__")[-1])
        if m:
            return m.group(1)
    return ""


def _address_from_span(span: etree._Element) -> LegalAddress:
    """Walk parent chain of a corrigendum span to build its LegalAddress."""
    chapter = section = subsection = None
    el = span.getparent()
    while el is not None:
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in ("preamble", "formula"):
            return LegalAddress(path=(("preamble", "formula"),))
        if tag == "section" and section is None:
            section = _extract_num_from_el(el)
        elif tag == "subsection" and subsection is None:
            subsection = _extract_num_from_el(el)
        elif tag in ("chapter", "hcontainer") and chapter is None:
            n = _extract_num_from_el(el)
            if n:
                chapter = n
        el = el.getparent()

    path: list[TreePathStep] = []
    if chapter:
        path.append(("chapter", chapter))
    if section:
        path.append(("section", section))
    if subsection:
        path.append(("subsection", subsection))
    return LegalAddress(path=tuple(path) if path else (("text", ""),))


def extract_inline_corrections(
    xml_bytes: bytes, statute_id: str
) -> tuple[list[LegalOperation], bytes]:
    """Population A: extract correction ops from <span class="corrigendum"> in AKN XML.

    Returns (ops, cleaned_bytes):
    - ops: one LegalOperation per corrigendum span that has an authorialNote.
      action="text_replace" with typed text_patch as the authoritative text
      carrier.
    - cleaned_bytes: xml_bytes with <authorialNote> elements removed only from
      matched corrigendum spans. The span retains only the corrected text —
      editorial metadata gone.

    Safe to call on any XML: returns ([], xml_bytes) if no corrigendum spans found.
    Handles both base statute body corrections and amendment johtolause corrections.

    TODO (full timeline reconstruction): Currently grafter discards ops and uses only
    cleaned_bytes, starting replay from the already-corrected state (3). Ideal chain:
    (1) statute published with wrong text → (2) corrigendum op applied → (3) corrected
    state = the pre-correction corpus state. To model: start master from
    text_patch.selector.match_text (or legacy notes[0]), insert op into timeline at
    correction year, replay normally. Requires CorpusGraph.corrigenda (spec item 7)
    + feeding ops to compile_timelines. Worth doing for pre-correction state queries
    or full provenance tracking.
    """
    if b"corrigendum" not in xml_bytes and b"authorialNote" not in xml_bytes:
        return [], xml_bytes

    try:
        root = parse_corpus_xml(xml_bytes)
    except etree.XMLSyntaxError:
        return [], xml_bytes

    from typing import cast
    spans = cast(list[etree._Element], root.xpath('//*[@class="corrigendum"]'))
    if not spans:
        return [], xml_bytes

    ops: list[LegalOperation] = []
    seq = 0

    for span_index, span in enumerate(spans):
        notes = span.findall(".//{*}authorialNote")
        if not notes:
            notes = span.findall(".//authorialNote")
        if not notes:
            _record_misapplied(
                op_id=f"corr/inline/{statute_id}/span/{span_index}",
                amendment_id=f"inline/{statute_id}",
                statute_id=statute_id,
                reason="FINLAND.INLINE_CORRIGENDUM_MISSING_AUTHORIAL_NOTE",
                wrong_text="",
                correct_text=(span.text or "").strip(),
                target=_address_from_span(span).path,
                note_count=0,
            )
            continue

        note = notes[0]

        # Corrected text = span.text (what Finlex has now)
        corrected_text = (span.text or "").strip()

        # Original wrong text = second <p> in authorialNote (not the "alkuperäinen" label)
        wrong_text = ""
        corr_year = ""
        for p in note.iter("{*}p"):
            p_text = (p.text or "").strip()
            if not p_text:
                continue
            if "alkuperäinen" in p_text.lower() or "oikaistu" in p_text.lower():
                m = _CORR_YEAR_RE.search(p_text)
                if m:
                    corr_year = m.group(1)
            else:
                wrong_text = p_text

        if not wrong_text:
            _record_misapplied(
                op_id=f"corr/inline/{statute_id}/span/{span_index}",
                amendment_id=f"inline/{statute_id}",
                statute_id=statute_id,
                reason="FINLAND.INLINE_CORRIGENDUM_MISSING_WRONG_TEXT",
                wrong_text="",
                correct_text=corrected_text,
                target=_address_from_span(span).path,
                corr_year=corr_year,
                note_count=len(notes),
                note_paragraph_count=sum(1 for _ in note.iter("{*}p")),
            )
            continue

        target = _address_from_span(span)
        corr_source = f"corr/inline/{statute_id}"
        seq += 1

        ops.append(_corrigendum_text_replace_op(
            op_id=f"corr/inline/{statute_id}/{seq}",
            sequence=seq,
            target=target,
            wrong_text=wrong_text,
            correct_text=corrected_text,
            source=OperationSource(
                statute_id=corr_source,
                enacted=corr_year,
                effective=corr_year,
                raw_text=corrected_text,
                corrected_by=corr_source,
            ),
        ))

    # Remove authorialNote elements only from matched corrigendum spans.
    # Unrelated authorial notes elsewhere in the XML must survive.
    for span in spans:
        for note in cast(list[etree._Element], span.xpath('.//*[local-name()="authorialNote"]')):
            parent = note.getparent()
            if parent is not None:
                parent.remove(note)
    cleaned = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    return ops, cleaned


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent  # src/lawvm/finland/ → LawVM/

# Source-defect fixes (LawVM-authored; no upstream corrigendum). The carrier
# file separately from upstream-corrigenda retries (see ``_RETRY_OVERLAYS``)
# because the two are different proof problems: a source-defect patch's
# authority comes from a typed source witness (typographic impossibility,
# multi-acquisition corroboration), whereas a retry overlay's authority
# comes from the upstream Finlex corrigendum PDF. Conflating them in one
# file produced the "first entry REPLACES all DB-derived patches for
# amendment_id" semantics that silently wiped legitimate sibling patches
# when any retry landed (AGENTS.md §0 Mutation Boundary, §1.8 conservation).
_SOURCE_DEFECT_YAML = _LAWVM_DIR / "data" / "finland" / "source_defect_fixes_fi.yaml"

# Upstream-corrigenda extraction retries — per-``stable_id`` overlay records.
# Each overlay targets one official-row ``stable_id`` and emits one or more
# byte-exact ``patches`` (``wrong_text``, ``correct_text``) that, applied
# together, realise the upstream-corrigendum effect. The overlay REPLACES
# only the targeted official row's auto-extracted (wrong, correct) candidate
# — never wiping siblings in the same amendment, unlike the legacy
# per-amendment REPLACE semantics.
#
# The canonical path constant lives in ``corrigendum_records`` so tools that
# read OR write overlays share one source of truth (the records module owns
# all ``corrigendum_*_fi.jsonl`` paths).
_WHITESPACE_RE = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# Regex-first parser
# ---------------------------------------------------------------------------

# Matches a correction block: "Sivulla(/) N, LOCATION on:\nWRONG\nPitää olla:\nCORRECT"
# The WRONG text ends just before "Pitää olla:" and CORRECT text ends before the
# next "Sivu..." block or end of text.
#
# Location prefix forms seen in corpus:
#   "Sivulla N"  — adessive singular (most common)
#   "Sivuilla N" — adessive plural (pages N–M)
#   "Sivu N"     — nominative/abbreviation (non-standard but occurs)
# Verb forms after location:
#   "on:"  — singular predicate (most common)
#   "ovat:" — plural predicate (when correcting multiple items)
_SIVU_PREFIX = r"Sivu[a-zäöå]*"  # matches Sivulla / Sivuilla / Sivu / etc.

# Per-line patterns used by the state-machine parser (no DOTALL, bounded by line length).
# Replacing _BLOCK_RE (re.DOTALL + nested .*?) which caused O(n²) backtracking on
# texts that have "on:" sequences but malformed or absent "Pitää olla:" blocks.
_LOCATION_LINE_RE = re.compile(
    # on/ovat: standard (may be followed by adverb: "on virheellisesti:")
    # kuuluu/kuuluvat: older "reads:"; lukee: less common
    # "joka kuuluu:" used after "puuttuu" (ADD block — text to insert)
    rf"^({_SIVU_PREFIX}\s.+)\s+(?:on(?:\s+\w+)?|ovat(?:\s+\w+)?|kuuluu|kuuluvat|lukee):\s*$"
)
# Location contains "puuttuu" → ADD block (collected text is what to insert, not wrong text)
_PUUTTUU_RE = re.compile(r"\bpuuttuu\b", re.IGNORECASE)
_PITAA_OLLA_RE = re.compile(
    # Pitää olla: standard; Kuuluu olla: older variant; Tulee olla: rarer variant
    r"^(?:Pitää\s+olla|Kuuluu\s+olla|Tulee\s+olla):\s*$"
)
# pdftotext running page header emitted at page breaks — skip when collecting
# wrong/correct lines; it has no correction content.
_CORR_PAGE_HDR_RE = re.compile(r"^Oikaisuja Suomen [Ss]äädöskokoelmaan\s*$", re.IGNORECASE)

# Extract amendment ID from PDF header.
# Standard form: "Suomen säädöskokoelmaan n:o 984/2018"
# Older form:    "Suomen säädöskokoelmaan 984/2018"  (no n:o prefix)
_HEADER_ID_RE = re.compile(r"säädöskokoelmaan\s+(?:n:o\s+)?(\d+/\d{4})")

# Location keyword patterns for type classification
_JOHTOLAUSE_KWS = {"johtolause", "johtolauseen", "johtolauseessa", "johtolauseessa,"}
_TABLE_KWS = {"liite", "liitteessä", "liitteen", "taulukko", "taulukossa"}
_FOOTNOTE_KWS = {"alaviite", "alaviitteessä", "viitteessä"}
_BARE_SECTION_NUM_RE = re.compile(r"^\s*(\d+)\s*([a-z]?)\s*§\.?\s*$", re.IGNORECASE)


# FUTURE: LLM location parsing (Opus suggestion)
# The hybrid approach: regex splits PDF into blocks (rigid "on:/Pitää olla:" format),
# LLM parses each block's location description → LegalAddress + correction_type.
# Same pattern as mev/detectors/lausunto_noise.py (structured prompt → JSON → parsed result).
# Needed because complex locations like "47 luvun 1 §:n 1 momentin johdantokappale ja 1 kohta"
# map to multiple LegalAddresses — essentially the same Finnish legal grammar as johtolause PEG.
# Current regex _location_to_address() is V1 sufficient for text-replacement patching
# (LegalAddress is metadata, not used for patch lookup). Upgrade when graph integration matters.
#
# Prompt sketch for location LLM:
#   Input: location_text (e.g. "47 luvun 1 §:n 1 momentin johdantokappale")
#   Output JSON: {"path": [["chapter","47"],["section","1"],["subsection","1"]],
#                 "special": "intro", "type": "prose"}
# Use LLAMA_API_BASE from mev.config (or fall back to localhost:8080).


def _classify_location(location: str) -> str:
    loc = location.lower()
    if "johtolauseen jälkeen" in loc:
        return "prose"
    if any(kw in loc for kw in _JOHTOLAUSE_KWS):
        return "johtolause"
    if any(kw in loc for kw in _TABLE_KWS):
        return "table"
    if any(kw in loc for kw in _FOOTNOTE_KWS):
        return "footnote"
    return "prose"


def _is_section_num_before_heading_location(location: str) -> bool:
    loc = _WHITESPACE_RE.sub(" ", _normalize_ws(location)).strip().lower()
    return (
        "pykälän otsikon edellä" in loc
        or "pykälänumerona" in loc
        or "pykälän numero" in loc
    )


def _bare_section_num_display(text: str) -> str:
    match = _BARE_SECTION_NUM_RE.match(_normalize_ws(text))
    if not match:
        return ""
    number, suffix = match.groups()
    return f"{number} {suffix.lower()} §".strip() if suffix else f"{number} §"


def _effective_corrigendum_type(corr_type: str, location: str) -> str:
    """Return the executable patch lane for one corrigendum row.

    Official records occasionally label a johtolause correction as generic
    ``prose`` even when ``location_desc`` explicitly says the correction is in
    the johtolause. Those rows must patch the amendment clause before PEG
    parsing, not the amendment body.
    """
    corr_type_norm = str(corr_type or "").strip()
    if corr_type_norm in _STATUTE_BODY_TYPES:
        location_type = _classify_location(location)
        if location_type == "johtolause":
            return "johtolause"
    return corr_type_norm


# Parse Finnish provision location into LegalAddress (best-effort)
_CHAPTER_RE = re.compile(r"(\d+)\s+luvun")
_SECTION_RE = re.compile(r"(\d+[a-z]?)\s+§")
_SUBSECTION_RE = re.compile(r"(\d+)\s+moment")


def _location_to_address(location: str, corr_type: str) -> LegalAddress:
    """Best-effort parse of Finnish location string to LegalAddress.

    Examples:
      "johtolauseen muutetaan-kohdassa" → (("johtolause", ""),)
      "47 luvun 1 §:n 1 momentissa"    → (("chapter","47"),("section","1"),("subsection","1"))
      "voimaantulosäännöksen 4 momentti" → (("section", "voimaantulo"), ("subsection","4"))
    """
    if corr_type == "johtolause":
        return LegalAddress(path=(("johtolause", ""),))

    path: list[Tuple[str, str]] = []
    chapter_m = _CHAPTER_RE.search(location)
    section_m = _SECTION_RE.search(location)
    subsection_m = _SUBSECTION_RE.search(location)

    if chapter_m:
        path.append(("chapter", chapter_m.group(1)))
    if section_m:
        path.append(("section", section_m.group(1)))
    elif "voimaantulo" in location.lower():
        path.append(("section", "voimaantulo"))
    if subsection_m:
        path.append(("subsection", subsection_m.group(1)))

    if not path:
        path.append(("text", ""))
    return LegalAddress(path=tuple(path))


def count_corrigendum_pairs(pdf_text: str) -> int:
    """Count On:/Pitää olla: correction pairs in raw PDF text using regex.

    Returns expected number of correction pairs extracted from the PDF.

    Counts occurrences of the wrong-side header marker — the location line
    ending in 'on:', 'ovat:', 'kuuluu:', 'kuuluvat:', or 'lukee:' (as matched
    by _LOCATION_LINE_RE).  Each such line introduces exactly one wrong→correct
    pair, so the count equals the expected number of extracted records.

    'puuttuu' (ADD) blocks are excluded because they produce no text_replace op
    and are not counted as extraction pairs.
    """
    count = 0
    for line in pdf_text.splitlines():
        m = _LOCATION_LINE_RE.match(line)
        if m and not _PUUTTUU_RE.search(m.group(1)):
            count += 1
    return count


def parse_corrigendum(pdf_text: str, amendment_id: str) -> ParsedCorrigendumResult:
    """Parse a Finnish corrigendum PDF text into LegalOperation list.

    amendment_id: NUM/YEAR format (e.g. "984/2018") — from the corrigendum filename
                  or header. If None/empty, operations still have op_ids using the
                  corrigendum source statute ID.

    Each "on:...Pitää olla:..." pair becomes one LegalOperation with:
      action = "text_replace"
      target = LegalAddress parsed from location string (best-effort)
      text_patch = typed selector/replacement payload
      legacy text_match/text_replacement remain unset
      source.statute_id = f"corr/{amendment_id}" (corrigendum as source statute)
      source.corrected_by = amendment_id (which amendment this corrects)
    """
    # Try to extract amendment_id from header if not provided
    if not amendment_id:
        m = _HEADER_ID_RE.search(pdf_text)
        amendment_id = m.group(1) if m else "unknown"

    corr_source_id = f"corr/{amendment_id}"
    ops: List[LegalOperation] = []
    unsupported_patches: List[UnsupportedCorrigendumPatch] = []

    # Fast bail-out: if no known separator or ADD marker is present, no blocks can exist.
    if not any(sep in pdf_text for sep in ("Pitää olla:", "Kuuluu olla:", "Tulee olla:", "puuttuu")):
        return ParsedCorrigendumResult(ops=())

    # State-machine parser: O(n) single pass, no backtracking.
    # States: scanning → in_wrong → in_correct → scanning ...
    lines = pdf_text.splitlines()
    seq = 0
    i = 0
    while i < len(lines):
        loc_m = _LOCATION_LINE_RE.match(lines[i])
        if not loc_m:
            i += 1
            continue
        location_raw = loc_m.group(1).strip()
        is_add = bool(_PUUTTUU_RE.search(location_raw))
        i += 1

        if is_add:
            # "puuttuu X, joka kuuluu:" — text that follows is what should be ADDED.
            # Collect until next location block or end; no "Pitää olla:" expected.
            add_lines: list[str] = []
            while i < len(lines) and not _LOCATION_LINE_RE.match(lines[i]):
                if not _CORR_PAGE_HDR_RE.match(lines[i]):
                    add_lines.append(lines[i])
                i += 1
            wrong_text = ""
            correct_text = "\n".join(add_lines).strip()
            if not correct_text:
                seq += 1
                corr_type = _classify_location(location_raw)
                target = _location_to_address(location_raw, corr_type)
                unsupported_patches.append(
                    UnsupportedCorrigendumPatch(
                        amendment_id=amendment_id,
                        sequence=seq,
                        correction_kind="ADD_EMPTY_BODY",
                        location=location_raw,
                        target=target,
                        correct_text="",
                        reason="FINLAND.CORRIGENDUM_ADD_EMPTY_BODY",
                        source_statute=corr_source_id,
                    )
                )
                continue
            seq += 1
            corr_type = _classify_location(location_raw)
            target = _location_to_address(location_raw, corr_type)
            unsupported_patches.append(
                UnsupportedCorrigendumPatch(
                    amendment_id=amendment_id,
                    sequence=seq,
                    correction_kind="ADD",
                    location=location_raw,
                    target=target,
                    wrong_text="",
                    correct_text=correct_text,
                    reason="FINLAND.CORRIGENDUM_ADD_UNSUPPORTED",
                    source_statute=corr_source_id,
                )
            )
            continue
        else:
            # Collect wrong_text lines until "Pitää olla:" (or variant).
            # Skip running page headers — pdftotext emits them at page breaks,
            # which can land inside a correction block spanning two pages.
            wrong_lines: list[str] = []
            while i < len(lines) and not _PITAA_OLLA_RE.match(lines[i]):
                if not _CORR_PAGE_HDR_RE.match(lines[i]):
                    wrong_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                break
            i += 1  # consume "Pitää olla:" line

            # Collect correct_text lines until next block start or end
            correct_lines: list[str] = []
            while i < len(lines) and not _LOCATION_LINE_RE.match(lines[i]):
                if not _CORR_PAGE_HDR_RE.match(lines[i]):
                    correct_lines.append(lines[i])
                i += 1
            # Do not advance i — outer loop will match the next location line directly.

            wrong_text = "\n".join(wrong_lines).strip()
            correct_text = "\n".join(correct_lines).strip()
            if not wrong_text or not correct_text:
                seq += 1
                corr_type = _classify_location(location_raw)
                target = _location_to_address(location_raw, corr_type)
                unsupported_patches.append(
                    UnsupportedCorrigendumPatch(
                        amendment_id=amendment_id,
                        sequence=seq,
                        correction_kind="REPLACE_EMPTY_WRONG" if not wrong_text else "REPLACE_EMPTY_CORRECT",
                        location=location_raw,
                        target=target,
                        wrong_text=wrong_text,
                        correct_text=correct_text,
                        reason=(
                            "FINLAND.CORRIGENDUM_REPLACE_EMPTY_WRONG"
                            if not wrong_text
                            else "FINLAND.CORRIGENDUM_REPLACE_EMPTY_CORRECT"
                        ),
                        source_statute=corr_source_id,
                    )
                )
                continue

        seq += 1
        corr_type = _classify_location(location_raw)
        target = _location_to_address(location_raw, corr_type)
        op = _corrigendum_text_replace_op(
            op_id=f"corr/{amendment_id}/{seq}",
            sequence=seq,
            target=target,
            wrong_text=wrong_text,
            correct_text=correct_text,
            source=OperationSource(
                statute_id=corr_source_id,
                raw_text=location_raw,
                corrected_by=amendment_id,
            ),
        )
        ops.append(op)

    return ParsedCorrigendumResult(
        ops=tuple(ops),
        unsupported_patches=tuple(unsupported_patches),
    )


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    """Normalize Unicode whitespace variants and collapse multiple spaces.

    Delegates to metadata._normalize_fi_parse_text which uses the exhaustive
    Unicode Zs category mapping rather than a hand-maintained codepoint list.
    """
    return _normalize_ws_base(text)


_ELLIPSIS_RE = re.compile(r"\s*(?:\.{3,}|(?:\. ){2,}\.|…)\s*")


def _split_on_ellipsis(text: str) -> List[str]:
    """Split text on '...' or '…' markers, returning non-empty stripped fragments."""
    return [p.strip() for p in _ELLIPSIS_RE.split(text) if p.strip()]


def _strip_paired_context_ellipsis_patch(wrong: str, correct: str) -> tuple[str, str] | None:
    """Return the concrete patch body for ``… wrong …`` / ``… correct …`` witnesses.

    Some official corrigenda quote only a middle johtolause fragment and wrap it
    in leading/trailing ellipses as context markers. Those markers are not source
    text and must not be written into the XML.
    """

    def strip_context(value: str) -> str | None:
        stripped = value.strip()
        leading_marker = ""
        for marker in ("...", "…"):
            if stripped.startswith(marker):
                leading_marker = marker
                break
        if not leading_marker:
            return None
        stripped = stripped[len(leading_marker):].strip()

        trailing_marker = ""
        for marker in ("...", "…"):
            if stripped.endswith(marker):
                trailing_marker = marker
                break
        if not trailing_marker:
            return None
        body = stripped[: -len(trailing_marker)].strip()
        return body or None

    wrong_body = strip_context(wrong)
    correct_body = strip_context(correct)
    if wrong_body is None or correct_body is None or wrong_body == correct_body:
        return None
    return wrong_body, correct_body


_PREAMBLE_RE = re.compile(rb"(<preamble\b[^>]*>)(.*?)(</preamble>)", re.DOTALL | re.IGNORECASE)
_TABLE_RE = re.compile(rb"(<table\b[^>]*>|<tblock\b[^>]*>)(.*?)(</table>|</tblock>)", re.DOTALL | re.IGNORECASE)


def _apply_scoped_replace(
    xml_bytes: bytes, wrong: str, correct: str, corr_type: str
) -> Tuple[bytes, bool]:
    """Apply text replacement scoped to the appropriate XML section by correction type.

    johtolause → search only within <preamble>...</preamble>
    table       → search only within <table>/<tblock> elements
    other       → full-document search via _apply_text_replace

    Scoping is critical for short wrong_text strings: '6 luku' is dangerous in
    a 100KB document but unique within a 500-byte preamble.
    """
    if corr_type == "johtolause":
        m = _PREAMBLE_RE.search(xml_bytes)
        if m:
            _pre_open, pre_body, _pre_close = m.group(1), m.group(2), m.group(3)
            patched_body, ok = _apply_text_replace(pre_body, wrong, correct)
            if ok:
                rebuilt = xml_bytes[:m.start(2)] + patched_body + xml_bytes[m.end(2):]
                return rebuilt, True
            return xml_bytes, False
        # No preamble tag — fall through to full-doc search
    elif corr_type == "table":
        # Try each table/tblock element in turn
        for m in _TABLE_RE.finditer(xml_bytes):
            tbl_body = m.group(2)
            patched_body, ok = _apply_text_replace(tbl_body, wrong, correct)
            if ok:
                rebuilt = xml_bytes[:m.start(2)] + patched_body + xml_bytes[m.end(2):]
                return rebuilt, True
        return xml_bytes, False

    return _apply_text_replace(xml_bytes, wrong, correct)


# Common PDF→XML encoding substitutions to try in addition to exact match.
# Each pair: (pdf_form, xml_form) — try replacing pdf_form with xml_form in wrong_text
# before passes 1–3.
_PDF_XML_SUBS = [
    ("+/-", "±"),   # ± — chemical/physical specs
    ("+-", "±"),
    ("–", "-"),     # endash → hyphen (PDF sometimes uses endash where XML has hyphen)
    ("-", "–"),     # hyphen → endash
    ("...", "…"),   # ellipsis
    ("…", "..."),
]

_TextReplaceMode = Literal[
    "exact",
    "pdf_substitution",
    "normalized",
    "collapsed_whitespace",
    "ellipsis_split",
    "tag_tolerant",
    "fuzzy_window",
]


def _apply_text_replace_deterministic(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, _TextReplaceMode | None]:
    """Apply only direct or normalization-based text replacement strategies."""
    wrong_b = wrong.encode("utf-8")
    correct_b = correct.encode("utf-8")

    if wrong_b in xml_bytes:
        return xml_bytes.replace(wrong_b, correct_b, 1), "exact"

    for pdf_form, xml_form in _PDF_XML_SUBS:
        if pdf_form in wrong:
            alt_wrong = wrong.replace(pdf_form, xml_form)
            alt_b = alt_wrong.encode("utf-8")
            if alt_b in xml_bytes:
                return xml_bytes.replace(alt_b, correct_b, 1), "pdf_substitution"

    try:
        xml_str = xml_bytes.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return xml_bytes, None

    xml_norm = _normalize_ws(xml_str)
    wrong_norm = _normalize_ws(wrong).strip()

    if wrong_norm and wrong_norm in xml_norm:
        correct_norm = _normalize_ws(correct)
        patched = xml_norm.replace(wrong_norm, correct_norm, 1)
        return patched.encode("utf-8"), "normalized"

    wrong_ws = re.sub(r"\s+", " ", wrong_norm).strip()
    xml_ws = re.sub(r"\s+", " ", xml_norm)
    if wrong_ws and wrong_ws in xml_ws:
        correct_norm = _normalize_ws(correct)
        patched = xml_ws.replace(wrong_ws, correct_norm, 1)
        return patched.encode("utf-8"), "collapsed_whitespace"

    if _ELLIPSIS_RE.search(wrong):
        wrong_parts = _split_on_ellipsis(wrong)
        correct_parts = _split_on_ellipsis(correct)
        if wrong_parts and correct_parts and len(wrong_parts) == len(correct_parts):
            w_ws_parts = [re.sub(r"\s+", " ", _normalize_ws(p)).strip() for p in wrong_parts]
            c_ws_parts = [_normalize_ws(p).strip() for p in correct_parts]
            if all(w and xml_ws.count(w) == 1 for w in w_ws_parts):
                patched_str = xml_ws
                any_applied = False
                for w_ws_frag, c_ws_frag in zip(w_ws_parts, c_ws_parts, strict=True):
                    if w_ws_frag and w_ws_frag in patched_str:
                        patched_str = patched_str.replace(w_ws_frag, c_ws_frag, 1)
                        any_applied = True
                if any_applied:
                    return patched_str.encode("utf-8"), "ellipsis_split"

    return xml_bytes, None


def _apply_text_replace_heuristic(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, _TextReplaceMode | None]:
    """Apply heuristic recovery after deterministic matching fails."""
    try:
        xml_str = xml_bytes.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return xml_bytes, None

    wrong_norm = _normalize_ws(wrong).strip()
    wrong_ws = re.sub(r"\s+", " ", wrong_norm).strip()

    wrong_words = wrong_ws.split()
    if 4 <= len(wrong_words) <= 200 and len(wrong_ws) < 2000:
        words_b = [w.encode("utf-8") for w in wrong_words]
        first_word_b = words_b[0]
        window_extra = max(len(wrong_ws) * 10 + 1000, 3000)

        def _match_word_intra_tag(xml_b: bytes, word_b: bytes, start: int, end: int) -> int:
            """Match word_b at start in xml_b, skipping any XML tags within the word.
            Returns position after the match, or -1 on failure."""
            wi = 0
            pos = start
            while wi < len(word_b) and pos < end:
                ch = xml_b[pos:pos + 1]
                if ch == b'<':
                    tag_end = xml_b.find(b'>', pos + 1, end)
                    if tag_end < 0:
                        return -1
                    pos = tag_end + 1
                elif xml_b[pos:pos + 1] == word_b[wi:wi + 1]:
                    pos += 1
                    wi += 1
                else:
                    return -1
            return pos if wi == len(word_b) else -1

        pos = 0
        while True:
            hit = xml_bytes.find(first_word_b, pos)
            if hit < 0:
                break
            win_end = min(len(xml_bytes), hit + window_extra)
            cur = hit + len(words_b[0])
            failed = False
            for word_b in words_b[1:]:
                while cur < win_end:
                    ch = xml_bytes[cur:cur + 1]
                    if ch in (b' ', b'\t', b'\n', b'\r'):
                        cur += 1
                    elif xml_bytes[cur] >= 0x80:
                        try:
                            cp = xml_bytes[cur:cur + 4].decode("utf-8", errors="ignore")[0]
                            if cp and cp.isspace():
                                cur += len(cp.encode("utf-8"))
                                continue
                        except IndexError:
                            pass
                        break
                    elif ch == b'<':
                        tag_end = xml_bytes.find(b'>', cur + 1, win_end)
                        if tag_end < 0:
                            failed = True
                            break
                        cur = tag_end + 1
                    else:
                        break
                if failed:
                    break
                if xml_bytes[cur:cur + len(word_b)] == word_b:
                    cur += len(word_b)
                else:
                    new_cur = _match_word_intra_tag(xml_bytes, word_b, cur, win_end)
                    if new_cur >= 0:
                        cur = new_cur
                    else:
                        failed = True
                        break
            if not failed:
                patched = xml_bytes[:hit] + _normalize_ws(correct).encode("utf-8") + xml_bytes[cur:]
                return patched, "tag_tolerant"
            pos = hit + 1

    if 12 <= len(wrong_ws) <= 300:
        wrong_words_list = wrong_ws.split()
        if len(wrong_words_list) >= 3:
            try:
                xml_plain = re.sub(r"<[^>]+>", " ", xml_str)
                xml_plain = re.sub(r"\s+", " ", xml_plain).strip()
                w_len = len(wrong_ws)
                win_range = max(3, w_len // 10)
                step = max(1, w_len // 6)

                best_ratio = 0.0
                best_i = -1
                best_w_var = w_len

                # Pre-filter: find candidate regions by searching for a
                # present, reasonably rare witness token.  Official
                # corrigendum snippets may start with abbreviated punctuation
                # ("...jos", etc.); using that absent first token forces a
                # full-document fuzzy scan on large XML.
                def _anchor_token() -> tuple[str, int] | None:
                    best: tuple[int, int, int, str, int] | None = None
                    for idx, token in enumerate(wrong_words_list):
                        if len(token) < 4 or not any(ch.isalnum() for ch in token):
                            continue
                        first = xml_plain.find(token)
                        if first < 0:
                            continue
                        occurrences = 1
                        cursor = first + 1
                        while occurrences < 3:
                            cursor = xml_plain.find(token, cursor)
                            if cursor < 0:
                                break
                            occurrences += 1
                            cursor += 1
                        # Prefer unique/rare and long tokens, preserving source
                        # order as a final deterministic tie-breaker.
                        candidate = (occurrences, -len(token), idx, token, wrong_ws.find(token))
                        if best is None or candidate < best:
                            best = candidate
                    if best is None:
                        return None
                    return best[3], max(0, best[4])

                _anchor = _anchor_token()
                _candidate_regions: list[tuple[int, int]] = []
                if _anchor is not None:
                    _anchor_word, _anchor_offset = _anchor
                    _search_start = 0
                    region_slack = max(step, w_len // 3)
                    while True:
                        _hit = xml_plain.find(_anchor_word, _search_start)
                        if _hit < 0:
                            break
                        _estimated_start = max(0, _hit - _anchor_offset)
                        _region_start = max(0, _estimated_start - region_slack)
                        _region_end = min(len(xml_plain), _estimated_start + w_len + region_slack)
                        if _candidate_regions and _region_start <= _candidate_regions[-1][1]:
                            # Merge overlapping regions
                            _candidate_regions[-1] = (_candidate_regions[-1][0], _region_end)
                        else:
                            _candidate_regions.append((_region_start, _region_end))
                        _search_start = _hit + 1
                elif len(xml_plain) <= 5000:
                    _candidate_regions.append((0, len(xml_plain)))
                else:
                    return xml_bytes, None

                for w_var in range(max(10, w_len - win_range), w_len + win_range + 1):
                    for _r_start, _r_end in _candidate_regions:
                        for i in range(_r_start, min(_r_end, len(xml_plain) - w_var + 1), step):
                            window = xml_plain[i : i + w_var]
                            score = Levenshtein.ratio(wrong_ws, window)
                            if score > best_ratio:
                                best_ratio = score
                                best_i = i
                                best_w_var = w_var

                if best_i >= 0:
                    for w_var in range(max(10, w_len - win_range), w_len + win_range + 1):
                        for i in range(max(0, best_i - step), min(len(xml_plain) - w_var + 1, best_i + step + 1)):
                            window = xml_plain[i : i + w_var]
                            score = Levenshtein.ratio(wrong_ws, window)
                            if score > best_ratio:
                                best_ratio = score
                                best_i = i
                                best_w_var = w_var

                best_match_text = xml_plain[best_i : best_i + best_w_var] if best_i >= 0 else ""

                if best_ratio >= 0.88 and best_match_text:
                    match_words = best_match_text.split()
                    if len(match_words) >= 2:
                        words_b2 = [w.encode("utf-8") for w in match_words]
                        first_b2 = words_b2[0]
                        window_extra2 = max(len(best_match_text) * 10 + 1000, 3000)
                        pos2 = 0
                        while True:
                            hit2 = xml_bytes.find(first_b2, pos2)
                            if hit2 < 0:
                                break
                            win_end2 = min(len(xml_bytes), hit2 + window_extra2)
                            cur2 = hit2 + len(words_b2[0])
                            failed2 = False
                            for word_b2 in words_b2[1:]:
                                while cur2 < win_end2:
                                    ch2 = xml_bytes[cur2 : cur2 + 1]
                                    if ch2 in (b" ", b"\t", b"\n", b"\r"):
                                        cur2 += 1
                                    elif xml_bytes[cur2] >= 0x80:
                                        try:
                                            cp2 = xml_bytes[cur2:cur2+4].decode("utf-8", errors="ignore")[0]
                                            if cp2 and cp2.isspace():
                                                cur2 += len(cp2.encode("utf-8"))
                                                continue
                                        except IndexError:
                                            pass
                                        break
                                    elif ch2 == b"<":
                                        te2 = xml_bytes.find(b">", cur2 + 1, win_end2)
                                        if te2 < 0:
                                            failed2 = True
                                            break
                                        cur2 = te2 + 1
                                    else:
                                        break
                                if failed2:
                                    break
                                if xml_bytes[cur2 : cur2 + len(word_b2)] == word_b2:
                                    cur2 += len(word_b2)
                                else:
                                    failed2 = True
                                    break
                            if not failed2:
                                patched = (
                                    xml_bytes[:hit2]
                                    + _normalize_ws(correct).encode("utf-8")
                                    + xml_bytes[cur2:]
                                )
                                return patched, "fuzzy_window"
                            pos2 = hit2 + 1
            except (MemoryError, OverflowError):
                pass

    return xml_bytes, None


def _apply_text_replace_with_mode(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, _TextReplaceMode | None]:
    """Apply deterministic replacement first, then explicit heuristic recovery."""
    patched, mode = _apply_text_replace_deterministic(xml_bytes, wrong, correct)
    if mode is not None:
        return patched, mode
    return _apply_text_replace_heuristic(xml_bytes, wrong, correct)


def _apply_text_replace(xml_bytes: bytes, wrong: str, correct: str) -> Tuple[bytes, bool]:
    """Try to replace wrong text in xml_bytes. Returns (patched, applied)."""
    patched, mode = _apply_text_replace_with_mode(xml_bytes, wrong, correct)
    if mode is not None:
        return patched, True
    return xml_bytes, False


def _expand_single_ellipsis_text_patch(
    xml_bytes: bytes,
    wrong: str,
    correct: str,
) -> tuple[str, str] | None:
    """Expand a single-ellipsis corrigendum witness against visible fragment text.

    Official corrigenda sometimes abbreviate the middle of a johtolause witness
    with ``…`` while the source XML contains the full carried context. Keep this
    narrow: only support one ellipsis in both strings, preserve the matched
    middle text from the live fragment, then hand the fully expanded pair back
    to the normal text-replace machinery.
    """
    ellipsis = "…"
    if wrong.count(ellipsis) != 1 or correct.count(ellipsis) != 1:
        return None

    wrong_pre, wrong_post = wrong.split(ellipsis, 1)
    correct_pre, correct_post = correct.split(ellipsis, 1)
    wrong_pre_norm = re.sub(r"\s+", " ", _normalize_ws(wrong_pre)).strip()
    wrong_post_norm = re.sub(r"\s+", " ", _normalize_ws(wrong_post)).strip()
    correct_pre_norm = re.sub(r"\s+", " ", _normalize_ws(correct_pre)).strip()
    correct_post_norm = re.sub(r"\s+", " ", _normalize_ws(correct_post)).strip()
    if not wrong_pre_norm or not wrong_post_norm:
        return None

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return None
    visible_norm = re.sub(
        r"\s+",
        " ",
        " ".join(" ".join(str(part) for part in el.itertext()) for el in root),
    ).strip()
    if not visible_norm:
        return None

    start = visible_norm.find(wrong_pre_norm)
    if start < 0:
        return None
    gap_start = start + len(wrong_pre_norm)
    end = visible_norm.find(wrong_post_norm, gap_start)
    if end < 0 or end < gap_start:
        return None

    middle = visible_norm[gap_start:end]
    expanded_wrong = f"{wrong_pre_norm}{middle}{wrong_post_norm}"
    expanded_correct = f"{correct_pre_norm}{middle}{correct_post_norm}"
    return expanded_wrong, expanded_correct


def _normalized_text_index_map(value: str) -> tuple[str, list[int], list[int]]:
    normalized_parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for match in re.finditer(r"\s+|\S+", value):
        token = match.group(0)
        if token.isspace():
            normalized_parts.append(" ")
            starts.append(match.start())
            ends.append(match.end())
            continue
        normalized_parts.append(token)
        token_start = match.start()
        for idx, _ in enumerate(token):
            starts.append(token_start + idx)
            ends.append(token_start + idx + 1)
    return "".join(normalized_parts), starts, ends


def _global_normalized_text_index_map(value: str) -> tuple[str, list[int], list[int]]:
    """Collapse whitespace across concatenated text slots while preserving offsets."""

    normalized_parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for match in re.finditer(r"\s+|\S+", value):
        token = match.group(0)
        if token.isspace():
            if normalized_parts and normalized_parts[-1] != " ":
                normalized_parts.append(" ")
                starts.append(match.start())
                ends.append(match.end())
            continue
        token_start = match.start()
        for idx, cp in enumerate(token):
            normalized_parts.append(cp)
            starts.append(token_start + idx)
            ends.append(token_start + idx + 1)
    while normalized_parts and normalized_parts[-1] == " ":
        normalized_parts.pop()
        starts.pop()
        ends.pop()
    return "".join(normalized_parts), starts, ends


def _iter_text_slots_in_document_order(
    root: etree._Element,
) -> Iterable[tuple[etree._Element, str, str]]:
    """Yield element text/tail slots in XML document order."""

    def _walk(el: etree._Element) -> Iterable[tuple[etree._Element, str, str]]:
        if el.text:
            yield (el, "text", el.text)
        for child in el:
            yield from _walk(child)
            if child.tail:
                yield (child, "tail", child.tail)

    yield from _walk(root)


def _apply_visible_text_delta_single_slot(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, bool]:
    """Patch one text slot after matching the full visible text across XML nodes.

    This is for corrigenda where the witness string spans multiple XML nodes
    (for example ``<affectedDocument>`` in johtolause or ``<num>`` + ``<heading>``
    in a section body) but the actual changed character(s) still live inside one
    underlying text or tail slot.
    """
    if "<" in correct or ">" in correct:
        return xml_bytes, False

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return xml_bytes, False

    wrong_norm = re.sub(r"\s+", " ", _normalize_ws(wrong)).strip()
    correct_norm = re.sub(r"\s+", " ", _normalize_ws(correct)).strip()
    if not wrong_norm or not correct_norm:
        return xml_bytes, False

    prefix_len = 0
    while (
        prefix_len < len(wrong_norm)
        and prefix_len < len(correct_norm)
        and wrong_norm[prefix_len] == correct_norm[prefix_len]
    ):
        prefix_len += 1

    suffix_len = 0
    wrong_rest = len(wrong_norm) - prefix_len
    correct_rest = len(correct_norm) - prefix_len
    while (
        suffix_len < wrong_rest
        and suffix_len < correct_rest
        and wrong_norm[len(wrong_norm) - suffix_len - 1] == correct_norm[len(correct_norm) - suffix_len - 1]
    ):
        suffix_len += 1

    old = wrong_norm[prefix_len : len(wrong_norm) - suffix_len if suffix_len else len(wrong_norm)]
    new = correct_norm[prefix_len : len(correct_norm) - suffix_len if suffix_len else len(correct_norm)]
    if old == new:
        return xml_bytes, False
    old_norm = re.sub(r"\s+", " ", _normalize_ws(old)).strip()
    before_ctx = wrong[max(0, prefix_len - 16) : prefix_len]
    after_ctx = correct[
        len(correct) - suffix_len : min(len(correct), len(correct) - suffix_len + 16)
    ] if suffix_len else ""
    before_ctx_norm = re.sub(r"\s+", " ", _normalize_ws(before_ctx))
    after_ctx_norm = re.sub(r"\s+", " ", _normalize_ws(after_ctx)) if after_ctx else ""

    def _visible_pattern(text: str) -> str:
        escaped = re.escape(text)
        escaped = escaped.replace(r"\ ", r"\s+")
        escaped = escaped.replace(r"\(", r"\(\s*")
        escaped = escaped.replace(r"\)", r"\s*\)")
        escaped = escaped.replace(r"\[", r"\[\s*")
        escaped = escaped.replace(r"\]", r"\s*\]")
        return escaped

    slots: list[tuple[etree._Element, str, str]] = []
    flat_parts: list[str] = []
    flat_slot_map: list[tuple[int, int, int]] = []
    for el, field, value in _iter_text_slots_in_document_order(root):
        slot_index = len(slots)
        slots.append((el, field, value))
        normalized_value, starts, ends = _normalized_text_index_map(value)
        if not normalized_value:
            continue
        flat_parts.append(normalized_value)
        for i in range(len(normalized_value)):
            if i >= len(starts) or i >= len(ends):
                return xml_bytes, False
            flat_slot_map.append((slot_index, starts[i], ends[i]))

    flat_text = "".join(flat_parts)
    pattern_parts: list[str] = []
    if before_ctx_norm:
        pattern_parts.append(_visible_pattern(before_ctx_norm))
    pattern_parts.append(f"(?P<delta>{_visible_pattern(old_norm)})")
    if after_ctx_norm:
        pattern_parts.append(_visible_pattern(after_ctx_norm))
    pattern = "".join(pattern_parts)
    matches = list(re.finditer(pattern, flat_text))
    if len(matches) != 1:
        return xml_bytes, False
    hit = matches[0].start("delta")
    matched_old = matches[0].group("delta")

    if old:
        change_start = hit
        change_end = hit + len(matched_old)
        if change_end > len(flat_slot_map):
            return xml_bytes, False
        affected = flat_slot_map[change_start:change_end]
        if not affected:
            return xml_bytes, False
        slot_ids = {slot_id for slot_id, _start, _end in affected}
        if len(slot_ids) != 1:
            return xml_bytes, False
        slot_id = next(iter(slot_ids))
        orig_start = min(start for _slot_id, start, _end in affected)
        orig_end = max(end for _slot_id, _start, end in affected)
    else:
        if hit > len(flat_slot_map):
            return xml_bytes, False
        boundary_index = hit
        anchor_candidates: list[tuple[int, int]] = []
        if 0 <= boundary_index - 1 < len(flat_slot_map):
            slot_id, _start, end = flat_slot_map[boundary_index - 1]
            anchor_candidates.append((slot_id, end))
        if 0 <= boundary_index < len(flat_slot_map):
            slot_id, start, _end = flat_slot_map[boundary_index]
            anchor_candidates.append((slot_id, start))
        if not anchor_candidates:
            return xml_bytes, False
        if len({slot_id for slot_id, _pos in anchor_candidates}) != 1:
            return xml_bytes, False
        slot_id, pos = anchor_candidates[0]
        orig_start = pos
        orig_end = pos

    el, field, value = slots[slot_id]
    replaced = value[:orig_start] + new + value[orig_end:]
    if field == "text":
        el.text = replaced
    else:
        el.tail = replaced

    try:
        repaired = _serialize_fragment_root(root)
        if _parse_fragment_root(repaired) is None:
            return xml_bytes, False
    except etree.XMLSyntaxError:
        return xml_bytes, False
    return repaired, True


def _apply_visible_text_delta_multi_slot(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, bool]:
    """Patch a unique visible-text witness when the changed spans hit multiple slots.

    This is a bounded recovery for johtolause corrigenda whose witness text is
    unique in the fragment, but the changed characters live in more than one
    underlying text/tail slot. We still preserve XML structure by applying the
    per-slot edits directly instead of flattening the fragment into plain text.
    """
    if "<" in correct or ">" in correct:
        return xml_bytes, False

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return xml_bytes, False

    wrong_norm = re.sub(r"\s+", " ", _normalize_ws(wrong)).strip()
    correct_norm = re.sub(r"\s+", " ", _normalize_ws(correct)).strip()
    if not wrong_norm or not correct_norm:
        return xml_bytes, False

    slots: list[tuple[etree._Element, str, str]] = []
    flat_parts: list[str] = []
    flat_slot_map: list[tuple[int, int, int]] = []
    for el, field, value in _iter_text_slots_in_document_order(root):
        slot_index = len(slots)
        slots.append((el, field, value))
        normalized_value, starts, ends = _normalized_text_index_map(value)
        if not normalized_value:
            continue
        flat_parts.append(normalized_value)
        for i in range(len(normalized_value)):
            if i >= len(starts) or i >= len(ends):
                return xml_bytes, False
            flat_slot_map.append((slot_index, starts[i], ends[i]))

    flat_text = "".join(flat_parts)
    flat_norm, flat_norm_starts, flat_norm_ends = _global_normalized_text_index_map(flat_text)
    hit = flat_norm.find(wrong_norm)
    if hit < 0 or flat_norm.find(wrong_norm, hit + 1) >= 0:
        return xml_bytes, False

    matcher = difflib.SequenceMatcher(None, wrong_norm, correct_norm, autojunk=False)
    per_slot_edits: dict[int, list[tuple[int, int, str]]] = {}

    def _flat_span_from_normalized_span(start: int, end: int) -> tuple[int, int] | None:
        if start < 0 or end < start:
            return None
        if end == start:
            return (start, end)
        norm_start = hit + start
        norm_end = hit + end
        if norm_end > len(flat_norm_starts):
            return None
        return flat_norm_starts[norm_start], flat_norm_ends[norm_end - 1]

    def _flat_boundary_from_normalized_index(index: int) -> int | None:
        norm_index = hit + index
        if norm_index < 0:
            return None
        if norm_index < len(flat_norm_starts):
            return flat_norm_starts[norm_index]
        if norm_index == len(flat_norm_starts):
            return len(flat_text)
        return None

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag not in {"replace", "delete", "insert"}:
            return xml_bytes, False
        if tag == "insert":
            anchor_index = _flat_boundary_from_normalized_index(i1)
            if anchor_index is None:
                return xml_bytes, False
            candidates: list[tuple[int, int]] = []
            if 0 <= anchor_index - 1 < len(flat_slot_map):
                slot_id, _start, end = flat_slot_map[anchor_index - 1]
                candidates.append((slot_id, end))
            if 0 <= anchor_index < len(flat_slot_map):
                slot_id, start, _end = flat_slot_map[anchor_index]
                candidates.append((slot_id, start))
            if not candidates or len({slot_id for slot_id, _pos in candidates}) != 1:
                return xml_bytes, False
            slot_id, pos = candidates[0]
            per_slot_edits.setdefault(slot_id, []).append((pos, pos, correct_norm[j1:j2]))
            continue

        flat_span = _flat_span_from_normalized_span(i1, i2)
        if flat_span is None:
            return xml_bytes, False
        change_start, change_end = flat_span
        if change_end > len(flat_slot_map):
            return xml_bytes, False
        affected = flat_slot_map[change_start:change_end]
        if not affected:
            return xml_bytes, False
        slot_ids = {slot_id for slot_id, _start, _end in affected}
        if len(slot_ids) != 1:
            return xml_bytes, False
        slot_id = next(iter(slot_ids))
        orig_start = min(start for _slot_id, start, _end in affected)
        orig_end = max(end for _slot_id, _start, end in affected)
        per_slot_edits.setdefault(slot_id, []).append((orig_start, orig_end, correct_norm[j1:j2]))

    if len(per_slot_edits) < 2:
        return xml_bytes, False

    for slot_id, edits in per_slot_edits.items():
        el, field, value = slots[slot_id]
        updated = value
        for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
            updated = updated[:start] + replacement + updated[end:]
        if field == "text":
            el.text = updated
        else:
            el.tail = updated

    try:
        repaired = _serialize_fragment_root(root)
        if _parse_fragment_root(repaired) is None:
            return xml_bytes, False
    except etree.XMLSyntaxError:
        return xml_bytes, False
    return repaired, True


def _apply_text_replace_single_text_slot(
    xml_bytes: bytes, wrong: str, correct: str
) -> Tuple[bytes, bool]:
    """Apply a local text/tail replacement while preserving surrounding markup.

    This is a narrow recovery path for johtolause corrigenda where the broad
    byte/text replacement modes match visible text that spans multiple XML
    nodes. If the actual changed span lives wholly inside one text-bearing slot
    (`.text` or `.tail`), patch only that slot and keep the XML structure
    intact.
    """
    if "<" in correct or ">" in correct:
        return xml_bytes, False

    prefix_len = 0
    while prefix_len < len(wrong) and prefix_len < len(correct) and wrong[prefix_len] == correct[prefix_len]:
        prefix_len += 1

    suffix_len = 0
    wrong_rest = len(wrong) - prefix_len
    correct_rest = len(correct) - prefix_len
    while (
        suffix_len < wrong_rest
        and suffix_len < correct_rest
        and wrong[len(wrong) - suffix_len - 1] == correct[len(correct) - suffix_len - 1]
    ):
        suffix_len += 1

    old = wrong[prefix_len : len(wrong) - suffix_len if suffix_len else len(wrong)]
    new = correct[prefix_len : len(correct) - suffix_len if suffix_len else len(correct)]
    if old == new:
        return xml_bytes, False

    before_ctx = wrong[max(0, prefix_len - 16) : prefix_len]
    after_ctx = correct[len(correct) - suffix_len : min(len(correct), len(correct) - suffix_len + 16)] if suffix_len else ""

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return xml_bytes, False

    old_norm = re.sub(r"\s+", " ", _normalize_ws(old)).strip()
    before_ctx_norm = re.sub(r"\s+", " ", _normalize_ws(before_ctx))
    after_ctx_norm = re.sub(r"\s+", " ", _normalize_ws(after_ctx)) if after_ctx else ""

    matches: list[tuple[int, etree._Element, str, int, int]] = []
    for el in root.iter():
        for field, value in (("text", el.text), ("tail", el.tail)):
            if not value:
                continue
            if not old and new:
                normalized_value, starts, _ends = _normalized_text_index_map(value)
                prefix_norm = re.sub(r"\s+", " ", _normalize_ws(wrong[:prefix_len]))
                suffix_norm = re.sub(
                    r"\s+",
                    " ",
                    _normalize_ws(wrong[prefix_len : len(wrong) - suffix_len if suffix_len else len(wrong)] + wrong[len(wrong) - suffix_len :]),
                )
                prefix_anchor_max = min(12, len(prefix_norm))
                suffix_anchor_max = min(12, len(suffix_norm))
                if prefix_anchor_max < 3 or suffix_anchor_max < 3:
                    continue
                best_slot_match: tuple[int, int] | None = None
                best_slot_score = -1
                for cut in range(len(normalized_value) + 1):
                    before = normalized_value[:cut]
                    after = normalized_value[cut:]
                    for prefix_anchor_len in range(prefix_anchor_max, 2, -1):
                        prefix_anchor = prefix_norm[-prefix_anchor_len:]
                        if not before.endswith(prefix_anchor):
                            continue
                        for suffix_anchor_len in range(suffix_anchor_max, 2, -1):
                            suffix_anchor = suffix_norm[:suffix_anchor_len]
                            if not after.startswith(suffix_anchor):
                                continue
                            score = prefix_anchor_len + suffix_anchor_len
                            if score > best_slot_score:
                                pos = starts[cut] if cut < len(starts) else len(value)
                                best_slot_match = (pos, pos)
                                best_slot_score = score
                            break
                        if best_slot_score >= prefix_anchor_len + suffix_anchor_max:
                            break
                if best_slot_match is not None:
                    start, end = best_slot_match
                    matches.append((best_slot_score, el, field, start, end))
                continue
            if old not in value:
                normalized_value, starts, ends = _normalized_text_index_map(value)
                norm_hit = -1
                if old_norm and old_norm in normalized_value:
                    if before_ctx_norm and after_ctx_norm:
                        needle = before_ctx_norm + old_norm + after_ctx_norm
                        idx = normalized_value.find(needle)
                        if idx >= 0:
                            norm_hit = idx + len(before_ctx_norm)
                    elif before_ctx_norm:
                        needle = before_ctx_norm + old_norm
                        idx = normalized_value.find(needle)
                        if idx >= 0:
                            norm_hit = idx + len(before_ctx_norm)
                    elif after_ctx_norm:
                        needle = old_norm + after_ctx_norm
                        norm_hit = normalized_value.find(needle)
                    elif normalized_value.count(old_norm) == 1:
                        norm_hit = normalized_value.find(old_norm)
                    if norm_hit >= 0:
                        norm_end = norm_hit + len(old_norm)
                        if norm_end <= len(starts):
                            matches.append((len(old_norm), el, field, starts[norm_hit], ends[norm_end - 1]))
                continue
            hit = -1
            if before_ctx and after_ctx:
                needle = before_ctx + old + after_ctx
                idx = value.find(needle)
                if idx >= 0:
                    hit = idx + len(before_ctx)
            elif before_ctx:
                needle = before_ctx + old
                idx = value.find(needle)
                if idx >= 0:
                    hit = idx + len(before_ctx)
            elif after_ctx:
                needle = old + after_ctx
                hit = value.find(needle)
            elif value.count(old) == 1:
                hit = value.find(old)
            if hit >= 0:
                matches.append((len(old), el, field, hit, hit + len(old)))

    if not matches:
        return xml_bytes, False

    best_score = max(score for score, *_rest in matches)
    best_matches = [match for match in matches if match[0] == best_score]
    if len(best_matches) != 1:
        return xml_bytes, False

    _, el, field, start, end = best_matches[0]
    value = el.text if field == "text" else el.tail
    assert value is not None
    replaced = value[:start] + new + value[end:]
    if field == "text":
        el.text = replaced
    else:
        el.tail = replaced

    try:
        repaired = _serialize_fragment_root(root)
        if _parse_fragment_root(repaired) is None:
            return xml_bytes, False
    except etree.XMLSyntaxError:
        return xml_bytes, False
    return repaired, True


# ---------------------------------------------------------------------------
# Format conversion
# ---------------------------------------------------------------------------

def _to_grafter_mid(amendment_id: str) -> Optional[str]:
    """Convert DB amendment_id (NUM/YEAR or YEAR/NUM) to grafter YEAR/NUM format.

    Finnish statute IDs are written as NUM/YEAR in text (e.g. "984/2018").
    Grafter uses YEAR/NUM (e.g. "2018/984") matching Finlex AKN path structure.
    Some LLM extractions flip the order — disambiguate by identifying which
    component is a plausible year (1900–2050).

    Returns None if neither component looks like a year.
    """
    parts = amendment_id.split("/")
    if len(parts) != 2:
        return None
    a, b = parts
    try:
        a_int, b_int = int(a), int(b)
    except ValueError:
        return None
    if 1900 <= b_int <= 2050:
        # b is year — format is NUM/YEAR → return YEAR/NUM
        return f"{b}/{a}"
    if 1900 <= a_int <= 2050:
        # a is year — format is already YEAR/NUM
        return f"{a}/{b}"
    return None


# ---------------------------------------------------------------------------
# CorrigendumPatchTable
# ---------------------------------------------------------------------------


def _johtolause_byte_range(xml_bytes: bytes) -> Tuple[int, int]:
    """Return (start, end) byte range of the johtolause fragment in xml_bytes.

    Mirrors get_johtolause() element selection so patches land exactly where
    the PEG parser reads:

    1. Laki pattern: <preamble> contains block[substitutions|repeals|insertions].
       Return the whole <preamble>…</preamble> range (safe: no body text in it).
    2. Asetus/fallback: no operative blocks in preamble → johtolause is in sec_1.
       Return <section eId="sec_1">…</section> range.
    3. Nothing found → return (-1, -1) → caller skips patching.
    """
    # Pattern 1: laki — <preamble> with operative blocks
    p_start = xml_bytes.find(b"<preamble")
    p_end_tag = xml_bytes.find(b"</preamble>", p_start + 1) if p_start >= 0 else -1
    if p_start >= 0 and p_end_tag >= 0:
        preamble = xml_bytes[p_start:p_end_tag]
        has_blocks = any(
            needle in preamble
            for needle in (b'name="substitutions"', b'name="repeals"', b'name="insertions"')
        )
        if has_blocks:
            return p_start, p_end_tag + len(b"</preamble>")

    # Pattern 2: asetus — sec_1 fallback
    sec1_attr = xml_bytes.find(b'eId="sec_1"')
    if sec1_attr >= 0:
        tag_start = xml_bytes.rfind(b"<section", 0, sec1_attr)
        if tag_start >= 0:
            sec_end = xml_bytes.find(b"</section>", sec1_attr)
            if sec_end >= 0:
                return tag_start, sec_end + len(b"</section>")

    return -1, -1


_STATUTE_BODY_TYPES = frozenset({"prose", "footnote", "metadata", "sami_translation"})
_FRAGMENT_WRAPPER_OPEN = b'<root xmlns:finlex="http://data.finlex.fi/schema/finlex">'
_FRAGMENT_WRAPPER_CLOSE = b"</root>"


def _parse_fragment_root(xml_bytes: bytes) -> etree._Element | None:
    try:
        return parse_corpus_xml(_FRAGMENT_WRAPPER_OPEN + xml_bytes + _FRAGMENT_WRAPPER_CLOSE)
    except etree.XMLSyntaxError:
        return None


def _serialize_fragment_root(root: etree._Element) -> bytes:
    parts: list[bytes] = []
    if root.text:
        parts.append(root.text.encode("utf-8"))
    for child in root:
        parts.append(etree.tostring(child, encoding="utf-8"))
        if child.tail:
            parts.append(child.tail.encode("utf-8"))
    return b"".join(parts)

# ---------------------------------------------------------------------------
# Location-scoped fallback helpers for body patches
# ---------------------------------------------------------------------------

# Matches Finnish section number: digits optionally followed by a letter,
# then optional space, then §.  E.g. "92 §", "15 a §", "3§".
_LOC_SECTION_RE = re.compile(r"(\d+\s*[a-z]?)\s*§")
# Matches subsection (momentti): digits followed by 'moment' (momentti/momentissa/momentin/…)
_LOC_SUBSECTION_RE = re.compile(r"(\d+)\s+moment")


def _parse_location_section(location_desc: str) -> tuple[str | None, str | None]:
    """Parse location_desc to extract (section_number, subsection_number).

    Returns (sec, subsec) where sec is like '92' or '15a' and subsec is like '1' or None.
    Both may be None if not parseable.

    Examples:
      '92 §:n 1 momentin 2 rivillä' → ('92', '1')
      '15 a §:ssä'                  → ('15a', None)
      '6 §:n 2 momentissa'          → ('6', '2')
      '3 §:n 1 momentin 3 kohdassa' → ('3', '1')
    """
    sec_m = _LOC_SECTION_RE.search(location_desc)
    if not sec_m:
        return None, None
    # Normalize: strip internal whitespace between digits and letter suffix.
    sec = re.sub(r"\s+", "", sec_m.group(1))  # '15 a' → '15a', '92' → '92'
    sub_m = _LOC_SUBSECTION_RE.search(location_desc)
    subsec = sub_m.group(1) if sub_m else None
    return sec, subsec


def _normalize_section_num_display(text: str) -> str:
    match = re.search(r"(\d+)\s*([a-z]?)\s*§", _normalize_ws(text), re.IGNORECASE)
    if not match:
        return ""
    number, suffix = match.groups()
    suffix = suffix.lower()
    return f"{number} {suffix} §".strip() if suffix else f"{number} §"


def _local_xml_tag(el: etree._Element) -> str:
    return str(el.tag).split("}")[-1] if "}" in str(el.tag) else str(el.tag)


def _split_section_num_heading_witness(text: str) -> tuple[str, str] | None:
    lines = [re.sub(r"\s+", " ", _normalize_ws(line)).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) < 2:
        return None
    section_num = _normalize_section_num_display(lines[0])
    heading = lines[1]
    if not section_num or not heading:
        return None
    return section_num, heading


def _apply_section_num_heading_corrigendum(
    xml_bytes: bytes, wrong: str, correct: str
) -> tuple[bytes, bool]:
    """Patch a section ``<num>`` when corrigendum witness is ``num + heading``."""
    if "<" in correct or ">" in correct:
        return xml_bytes, False

    wrong_pair = _split_section_num_heading_witness(wrong)
    correct_pair = _split_section_num_heading_witness(correct)
    if wrong_pair is None or correct_pair is None:
        return xml_bytes, False
    wrong_num, wrong_heading = wrong_pair
    correct_num, correct_heading = correct_pair
    if wrong_heading != correct_heading or wrong_num == correct_num:
        return xml_bytes, False

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return xml_bytes, False

    candidates: list[tuple[etree._Element, etree._Element]] = []
    for section in root.findall(".//{*}section"):
        num_el = section.find("./{*}num")
        heading_el = section.find("./{*}heading")
        if num_el is None or heading_el is None:
            continue
        num_text = _normalize_section_num_display(num_el.text or "")
        heading_text = re.sub(r"\s+", " ", _normalize_ws(heading_el.text or "")).strip()
        if num_text == wrong_num and heading_text == wrong_heading:
            candidates.append((section, num_el))

    if len(candidates) != 1:
        return xml_bytes, False

    _section, num_el = candidates[0]
    num_el.text = correct_num
    try:
        repaired = _serialize_fragment_root(root)
        if _parse_fragment_root(repaired) is None:
            return xml_bytes, False
    except etree.XMLSyntaxError:
        return xml_bytes, False
    return repaired, True


def _apply_bare_section_num_corrigendum(
    xml_bytes: bytes, wrong: str, correct: str, location_desc: str
) -> tuple[bytes, bool]:
    """Patch a unique body ``<section><num>`` backed by a section-number corrigendum."""
    if not _is_section_num_before_heading_location(location_desc):
        return xml_bytes, False
    wrong_num = _bare_section_num_display(wrong)
    correct_num = _bare_section_num_display(correct)
    if not wrong_num or not correct_num or wrong_num == correct_num:
        return xml_bytes, False

    root = _parse_fragment_root(xml_bytes)
    if root is None:
        return xml_bytes, False

    candidates: list[etree._Element] = []
    for section in root.findall(".//{*}section"):
        num_el = section.find("./{*}num")
        if num_el is None:
            continue
        if _normalize_section_num_display(num_el.text or "") == wrong_num:
            candidates.append(num_el)

    if len(candidates) != 1:
        candidates = _section_num_corrigendum_sequence_candidates(
            candidates,
            correct_num=correct_num,
        )
    if len(candidates) != 1:
        return xml_bytes, False

    candidates[0].text = correct_num
    try:
        repaired = _serialize_fragment_root(root)
        if _parse_fragment_root(repaired) is None:
            return xml_bytes, False
    except etree.XMLSyntaxError:
        return xml_bytes, False
    return repaired, True


def _section_num_corrigendum_sequence_candidates(
    candidates: list[etree._Element],
    *,
    correct_num: str,
) -> list[etree._Element]:
    """Keep ambiguous section-number candidates backed by sibling sequence context."""
    correct_ord = _section_num_ordinal(correct_num)
    if correct_ord is None:
        return []
    sequenced: list[etree._Element] = []
    for num_el in candidates:
        section = num_el.getparent()
        if section is None:
            continue
        parent = section.getparent() if section is not None else None
        if parent is None:
            continue
        siblings = [child for child in parent if _local_xml_tag(child) == "section"]
        try:
            index = siblings.index(section)
        except ValueError:
            continue
        prev_ord = _nearest_section_num_ordinal(reversed(siblings[:index]))
        next_ord = _nearest_section_num_ordinal(iter(siblings[index + 1 :]))
        if prev_ord == correct_ord - 1 or next_ord == correct_ord + 1:
            sequenced.append(num_el)
    return sequenced


def _nearest_section_num_ordinal(sections: Iterable[etree._Element]) -> int | None:
    for section in sections:
        num_el = section.find("./{*}num")
        if num_el is None:
            continue
        ordinal = _section_num_ordinal(_normalize_section_num_display(num_el.text or ""))
        if ordinal is not None:
            return ordinal
    return None


def _section_num_ordinal(display: str) -> int | None:
    match = re.fullmatch(r"(\d+) §", display)
    if not match:
        return None
    return int(match.group(1))


def _find_element_range(xml_bytes: bytes, sec: str, subsec: str | None) -> tuple[int, int] | None:
    """Find the byte range of the element identified by (sec, subsec) in xml_bytes.

    Searches for eId attributes matching 'sec_{sec}' optionally with '__subsec_{subsec}'.
    The eId may have a chapter prefix: 'chp_X__sec_Y' or just 'sec_Y'.
    Returns (start, end) byte offsets encompassing the full element, or None if not found.
    """
    sec_b = sec.encode("utf-8")
    subsec_b = subsec.encode("utf-8") if subsec else None

    # Build pattern: eId="...sec_{sec}" or eId="...sec_{sec}__subsec_{subsec}"
    if subsec_b:
        pattern = (
            rb'eId="[^"]*sec_' + sec_b + rb'__subsec_' + subsec_b + rb'"'
        )
    else:
        # Match sec_{sec}" exactly — no further double-underscore allowed after sec num.
        # This prevents sec_1 matching sec_10 etc.
        pattern = rb'eId="[^"]*sec_' + sec_b + rb'(?:"|__(?!sec_)[^"]*")'

    m = re.search(pattern, xml_bytes)
    if not m:
        return None

    attr_pos = m.start()
    # Walk backward to find the opening tag '<'
    tag_start = xml_bytes.rfind(b"<", 0, attr_pos)
    if tag_start < 0:
        return None

    # Determine the element name (section/subsection/hcontainer/…)
    tag_end_bracket = xml_bytes.find(b">", tag_start)
    if tag_end_bracket < 0:
        return None
    tag_snippet = xml_bytes[tag_start:tag_end_bracket + 1]
    tag_name_m = re.match(rb"<(\w+)", tag_snippet)
    if not tag_name_m:
        return None
    element_name = tag_name_m.group(1)  # b"section" or b"subsection" etc.

    # Find the matching closing tag by counting nesting
    close_tag = b"</" + element_name + b">"
    open_tag_pat = b"<" + element_name
    depth = 1
    pos = tag_end_bracket + 1
    # Walk through the bytes tracking open/close tag depth
    while pos < len(xml_bytes) and depth > 0:
        next_close = xml_bytes.find(close_tag, pos)
        next_open = xml_bytes.find(open_tag_pat, pos)
        if next_close < 0:
            return None  # Malformed XML — abort
        if next_open >= 0 and next_open < next_close:
            depth += 1
            pos = next_open + len(open_tag_pat)
        else:
            depth -= 1
            if depth == 0:
                end = next_close + len(close_tag)
                return tag_start, end
            pos = next_close + len(close_tag)

    return None


class CorrigendumPatchTable:
    """Keyed by amendment_id in YEAR/NUM format (matches grafter's amendment_id parameter).

    Build: load_from_source() reads the git-tracked corrigendum text corpus.
    Apply: patch_source_xml(xml_bytes, amendment_id) → (patched_bytes, applied_op_ids).

    Two patch buckets:
    - _patches: johtolause corrections applied to the amendment's operative clause
      (preamble or sec_1 range) before PEG parsing.
    - _body_patches: prose/footnote corrections applied to the amendment body
      (the new text the amendment inserts) before section extraction.
      Each entry is (wrong, correct, location_desc) — location_desc is used as a
      fallback scope when the normal full-body replace fails.

    Empty table if the classified text corpus is absent — no error, just no patches.
    """

    def __init__(self) -> None:
        # dict[YEAR/NUM str → List[LegalOperation]] — johtolause patches keyed by amendment_id
        self._patches: dict[str, List[LegalOperation]] = {}
        # dict[YEAR/NUM str → List[Tuple[wrong, correct, location_desc]]] — body text patches keyed by amendment_id
        self._body_patches: dict[str, List[Tuple[str, str, str]]] = {}
        # unsupported/non-executable corrigendum records kept visible for auditability
        self._unsupported_patches: list[dict[str, object]] = []
        # dict[YEAR/NUM amendment_id → statute_id (YEAR/NUM)] — base statute for each amendment
        self._amendment_to_statute: dict[str, str] = {}
        self._loaded = False

    def __len__(self) -> int:
        return sum(len(v) for v in self._patches.values())

    def amendment_count(self) -> int:
        return len(self._patches)

    def body_patch_count(self) -> int:
        return sum(len(v) for v in self._body_patches.values())

    def unsupported_patches(self) -> tuple[dict[str, object], ...]:
        """Return non-executable corrigendum rows preserved for frontier reporting."""
        return tuple(dict(row) for row in self._unsupported_patches)

    @classmethod
    def load_from_source(
        cls,
        source_path: Optional[Path] = None,
        overlays_path: Optional[Path] = None,
        unresolvable_path: Optional[Path] = None,
    ) -> "CorrigendumPatchTable":
        """Load all classified corrections from the repo text corpus.

        Overlay path resolution precedence:
          1. explicit ``overlays_path`` argument (tests pinning the overlay
             layer in isolation should pass this directly),
          2. ``<source_path>.parent / corrigendum_retry_overlays_fi.jsonl``
             when a ``source_path`` is provided (so tests that point the
             official-records path at a tmp dir automatically get an
             empty overlay — they don't pick up the real repo file), and
          3. the module-level ``_RETRY_OVERLAYS_JSONL`` constant when
             ``source_path`` is None (production: real repo file).
        ``unresolvable_path`` follows the same precedence for the skip-only
        unresolvable overlay ledger.
        """
        table = cls()
        path = source_path or default_patch_records_path()
        rows = load_patch_records(path)
        if overlays_path is None:
            if source_path is not None:
                overlays_path = source_path.parent / _RETRY_OVERLAYS_JSONL.name
            else:
                overlays_path = _RETRY_OVERLAYS_JSONL
        if unresolvable_path is None:
            if source_path is not None:
                unresolvable_path = source_path.parent / _UNRESOLVABLE_YAML.name
            else:
                unresolvable_path = _UNRESOLVABLE_YAML
        if not rows:
            return table

        # Deduplicate: ellipsis style variants (… vs ...) from different extractors
        # that agree on (amendment_id, corr_type, wrong_norm, correct_norm) are the
        # same logical correction.  Keep only the first occurrence per key.
        _ell_re = re.compile(r"\s*(?:\.{3,}|(?:\. ){2,}\.|…)\s*")
        _seen: set[tuple[str, str, str, str, str]] = set()
        _kept_text_pairs: dict[str, list[tuple[str, str, str]]] = {}
        _kept_location_variants: dict[tuple[str, str], list[_NearDuplicatePatchCandidate]] = {}
        _near_duplicate_ratio_cache: dict[tuple[str, str], float] = {}

        def _contains_same_text_family(
            amendment_id: str,
            corr_type: str,
            wrong_norm: str,
            correct_norm: str,
        ) -> bool:
            if corr_type in _STATUTE_BODY_TYPES or corr_type == "body_text":
                compatible_types = _STATUTE_BODY_TYPES | {"body_text"}
            else:
                compatible_types = {corr_type}
            wrong_compact = "".join(wrong_norm.split())
            correct_compact = "".join(correct_norm.split())
            for kept_corr_type, kept_wrong_norm, kept_correct_norm in _kept_text_pairs.get(amendment_id, []):
                if "::" in kept_corr_type:
                    kept_corr_type = kept_corr_type.split("::", 1)[0]
                if kept_corr_type not in compatible_types:
                    continue
                kept_wrong_compact = "".join(kept_wrong_norm.split())
                kept_correct_compact = "".join(kept_correct_norm.split())
                if (
                    wrong_compact == kept_wrong_compact
                    and correct_compact == kept_correct_compact
                ):
                    return True
                wrong_overlap = (
                    wrong_norm in kept_wrong_norm
                    or kept_wrong_norm in wrong_norm
                )
                correct_overlap = (
                    correct_norm in kept_correct_norm
                    or kept_correct_norm in correct_norm
                )
                if wrong_overlap and correct_overlap:
                    return True
            return False

        def _is_near_duplicate_location_variant(
            amendment_id: str,
            corr_type: str,
            norm_location: str,
            wrong_compact: str,
            correct_compact: str,
        ) -> bool:
            if not norm_location:
                return False
            if corr_type not in _STATUTE_BODY_TYPES | {"body_text"}:
                return False
            if not wrong_compact or not correct_compact:
                return False

            def _can_reach_similarity(left_len: int, right_len: int) -> bool:
                return (2 * min(left_len, right_len)) / (left_len + right_len) >= 0.985

            def _compact_similarity(left: str, right: str) -> float:
                if left == right:
                    return 1.0
                key = (left, right)
                if key in _near_duplicate_ratio_cache:
                    return _near_duplicate_ratio_cache[key]
                ratio = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
                _near_duplicate_ratio_cache[key] = ratio
                return ratio

            for kept in _kept_location_variants.get((amendment_id, norm_location), ()):
                kept_wrong_compact = kept.wrong_compact
                kept_correct_compact = kept.correct_compact
                if not (
                    _can_reach_similarity(len(wrong_compact), len(kept_wrong_compact))
                    and _can_reach_similarity(len(correct_compact), len(kept_correct_compact))
                ):
                    continue
                wrong_ratio = _compact_similarity(wrong_compact, kept_wrong_compact)
                correct_ratio = _compact_similarity(correct_compact, kept_correct_compact)
                if wrong_ratio >= 0.985 and correct_ratio >= 0.985:
                    return True
            return False

        def _contains_same_compact_text_pair(
            amendment_id: str,
            corr_type: str,
            wrong_compact: str,
            correct_compact: str,
        ) -> bool:
            if corr_type in _STATUTE_BODY_TYPES or corr_type == "body_text":
                compatible_types = _STATUTE_BODY_TYPES | {"body_text"}
            else:
                compatible_types = {corr_type}
            for kept_corr_type, kept_wrong_norm, kept_correct_norm in _kept_text_pairs.get(amendment_id, []):
                if "::" in kept_corr_type:
                    kept_corr_type = kept_corr_type.split("::", 1)[0]
                if kept_corr_type not in compatible_types:
                    continue
                if (
                    wrong_compact == "".join(kept_wrong_norm.split())
                    and correct_compact == "".join(kept_correct_norm.split())
                ):
                    return True
            return False

        # -----------------------------------------------------------------
        # Upstream-corrigenda retry overlays — per-``stable_id`` authority
        # layer. Loaded BEFORE the per-row loop so overlay-targeted official
        # rows can be SKIPPED: their auto-extracted ``(wrong, correct)`` is
        # replaced by the overlay's byte-exact ``patches``. The overlay
        # REPLACES only the targeted row — never wiping sibling patches in
        # the same amendment (the §0 promotion chain made surgical instead
        # of wholesale, vs. the legacy per-amendment "REPLACE all DB
        # patches" semantics that silently destroyed legitimate corrigendum
        # content in the same amendment when any retry landed).
        # -----------------------------------------------------------------
        overlays = _load_retry_overlays(overlays_path)
        overlay_targeted_stable_ids: set[str] = set(overlays.keys())
        # Unresolvable-corrigendum overlays: per-``stable_id`` SKIP-only
        # records declaring no mechanical apply is possible (source missing,
        # anchor absent post-search, semantic-only, ambiguous anchor).
        # These official rows are skipped at load time (no patch emitted); a
        # typed finding is recorded per the schema at
        # ``corrigendum_unresolvable_fi.yaml``.
        overlay_targeted_stable_ids.update(
            load_unresolvable_overlay_stable_ids(unresolvable_path)
        )
        # Unique op_id counter per amendment within the retry-overlay loop. Each
        # overlay's local ``patch_idx`` starts at 0, so emitting
        # ``retry/<aid>/<patch_idx>`` would collide when two overlays target
        # the same amendment (e.g. expanded_corr stable_ids for 2021/669).
        # Maintain a per-amendment global counter to keep op_ids unique —
        # uniqueness is required because apply loop appends op_ids to the
        # ``applied`` list and downstream tracking (applied_list assertion,
        # misapplied ledger dedup) assumes uniqueness.
        overlay_op_counters: dict[str, int] = {}
        for overlay_stable_id, overlay in overlays.items():
            amendment_id_numyr = overlay.amendment_id_numyr
            if not amendment_id_numyr:
                continue
            amendment_id = _to_grafter_mid(amendment_id_numyr)
            if amendment_id is None:
                continue
            overlay_corr_type = overlay.correction_type
            op_seq = overlay_op_counters.get(amendment_id, 0)
            for patch_idx, patch in enumerate(overlay.patches):
                wrong = patch.wrong_text.strip()
                correct = patch.correct_text.strip()
                if not wrong or not correct or wrong == correct:
                    continue
                wrong_norm = _WHITESPACE_RE.sub(
                    " ", _normalize_ws(_ell_re.sub("…", wrong))
                ).strip()
                correct_norm = _WHITESPACE_RE.sub(
                    " ", _normalize_ws(_ell_re.sub("…", correct))
                ).strip()
                wrong_compact = "".join(wrong_norm.split())
                correct_compact = "".join(correct_norm.split())
                # Body text-family dedup — overlay patches are typed/curated,
                # so we apply the compact-pair dedup to avoid double-emit
                # within an amendment. The near-duplicate-location heuristic
                # is moot (overlay patches carry no location_desc).
                if (
                    overlay_corr_type in _STATUTE_BODY_TYPES | {"body_text"}
                    and _contains_same_compact_text_pair(
                        amendment_id,
                        overlay_corr_type,
                        wrong_compact,
                        correct_compact,
                    )
                ):
                    continue
                dedup_key = (
                    amendment_id,
                    overlay_corr_type,
                    "",
                    wrong_norm,
                    correct_norm,
                )
                if dedup_key in _seen:
                    continue
                _seen.add(dedup_key)
                _kept_text_pairs.setdefault(amendment_id, []).append(
                    (overlay_corr_type, wrong_norm, correct_norm)
                )
                op = _corrigendum_text_replace_op(
                    op_id=f"retry/{amendment_id_numyr}/{op_seq}",
                    sequence=op_seq,
                    target=_location_to_address("", overlay_corr_type),
                    wrong_text=wrong,
                    correct_text=correct,
                    source=OperationSource(
                        statute_id=f"corr/{amendment_id_numyr}",
                        raw_text=(
                            f"retry overlay on {overlay_stable_id}"
                            f" family={overlay.family!r}"
                            f" source_pdf_witness={overlay.source_pdf_witness!r}"
                        ),
                        corrected_by=amendment_id_numyr,
                        expected_apply_count=patch.expected_apply_count,
                    ),
                )
                op_seq += 1
                overlay_op_counters[amendment_id] = op_seq
                if (
                    overlay_corr_type in _STATUTE_BODY_TYPES
                    or overlay_corr_type == "body_text"
                ):
                    if amendment_id not in table._body_patches:
                        table._body_patches[amendment_id] = []
                    table._body_patches[amendment_id].append((wrong, correct, ""))
                elif overlay_corr_type != "table":
                    if amendment_id not in table._patches:
                        table._patches[amendment_id] = []
                    table._patches[amendment_id].append(op)
                else:
                    table._unsupported_patches.append(
                        {
                            "reason": "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED",
                            "amendment_id": amendment_id,
                            "source_amendment_id": amendment_id_numyr,
                            "statute_id": "",
                            "correction_type": overlay_corr_type,
                            "location_desc": "",
                            "wrong_text": wrong,
                            "correct_text": correct,
                        }
                    )

        for row in rows:
            # Skip overlay-targeted official rows: the overlay's byte-exact
            # patches have already been emitted above; processing this row's
            # auto-extracted (wrong, correct) would double-emit and re-introduce
            # the misapplied patch that the overlay exists to replace.
            row_stable_id = str(row.get("stable_id") or "").strip()
            if row_stable_id and row_stable_id in overlay_targeted_stable_ids:
                continue
            amendment_id_numyr = str(row.get("amendment_id") or "").strip()
            idx = int(row.get("correction_index") or 0)
            location = str(row.get("location_desc") or "")
            corr_type = _effective_corrigendum_type(
                str(row.get("correction_type") or "").strip(),
                location,
            )
            wrong = str(row.get("wrong_text") or "").strip()
            correct = str(row.get("correct_text") or "").strip()
            if (
                corr_type == "johtolause"
                and _is_section_num_before_heading_location(location)
                and _bare_section_num_display(wrong)
                and _bare_section_num_display(correct)
            ):
                corr_type = "prose"
            if (
                not amendment_id_numyr
                or not wrong
                or not correct
                or wrong == correct
                or row.get("parse_error")
            ):
                continue
            amendment_id = _to_grafter_mid(amendment_id_numyr)
            if amendment_id is None:
                continue

            # Track base statute (the statute that ultimately gets modified).
            statute_id_raw = str(row.get("statute_id") or "").strip()
            if statute_id_raw and amendment_id not in table._amendment_to_statute:
                table._amendment_to_statute[amendment_id] = statute_id_raw

            wrong_norm = _WHITESPACE_RE.sub(" ", _normalize_ws(_ell_re.sub("…", wrong))).strip()
            correct_norm = _WHITESPACE_RE.sub(" ", _normalize_ws(_ell_re.sub("…", correct))).strip()
            location_key = (
                _WHITESPACE_RE.sub(" ", _normalize_ws(location)).strip()
                if location and corr_type in _STATUTE_BODY_TYPES | {"body_text"}
                else ""
            )
            wrong_compact = "".join(wrong_norm.split())
            correct_compact = "".join(correct_norm.split())
            if (
                corr_type in _STATUTE_BODY_TYPES | {"body_text"}
                and _contains_same_compact_text_pair(
                    amendment_id,
                    corr_type,
                    wrong_compact,
                    correct_compact,
                )
            ):
                continue
            # Deduplicate across extractor variants: same amendment + location +
            # normalised wrong/correct (ellipsis style may differ between LLM and vision).
            dedup_key = (amendment_id, corr_type, location, wrong_norm, correct_norm)
            if dedup_key in _seen:
                continue

            # ``manual_expanded`` rows are helper expansions of an already-known
            # corrigendum span. When an official patch for the same amendment/type
            # already covers the same wrong→correct text family, applying the
            # expansion as an additional patch can double-rewrite the johtolause.
            # Keep the official patch and skip the expanded duplicate.
            if (
                str(row.get("extraction_source") or "").strip() == "manual_expanded"
                and _contains_same_text_family(amendment_id, corr_type, wrong_norm, correct_norm)
            ):
                continue
            if _is_near_duplicate_location_variant(
                amendment_id,
                corr_type,
                location_key,
                wrong_compact,
                correct_compact,
            ):
                continue
            _seen.add(dedup_key)
            kept_type = corr_type
            if location and corr_type in _STATUTE_BODY_TYPES | {"body_text"}:
                kept_type = f"{corr_type}::{location_key}"
            _kept_text_pairs.setdefault(amendment_id, []).append((kept_type, wrong_norm, correct_norm))
            if location and corr_type in _STATUTE_BODY_TYPES | {"body_text"}:
                _kept_location_variants.setdefault((amendment_id, location_key), []).append(
                    _NearDuplicatePatchCandidate(
                        wrong_compact=wrong_compact,
                        correct_compact=correct_compact,
                    )
                )

            op = _corrigendum_text_replace_op(
                op_id=f"corr/{amendment_id_numyr}/{idx}",
                sequence=idx,
                target=_location_to_address(location or "", corr_type),
                wrong_text=wrong,
                correct_text=correct,
                source=OperationSource(
                    statute_id=f"corr/{amendment_id_numyr}",
                    raw_text=location or "",
                    corrected_by=amendment_id_numyr,
                ),
            )

            if corr_type in _STATUTE_BODY_TYPES:
                # Prose/footnote/metadata corrections target the body text the
                # amendment inserts.  Apply to the amendment XML body range via
                # patch_source_body_xml (already called in process_muutoslaki).
                if amendment_id not in table._body_patches:
                    table._body_patches[amendment_id] = []
                table._body_patches[amendment_id].append((wrong, correct, location or ""))
            elif corr_type != "table":
                # johtolause corrections go to _patches (johtolause range).
                if amendment_id not in table._patches:
                    table._patches[amendment_id] = []
                table._patches[amendment_id].append(op)
            else:
                table._unsupported_patches.append(
                    {
                        "reason": "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED",
                        "amendment_id": amendment_id,
                        "source_amendment_id": amendment_id_numyr,
                        "statute_id": statute_id_raw,
                        "correction_type": corr_type,
                        "location_desc": location or "",
                        "wrong_text": wrong,
                        "correct_text": correct,
                    }
                )

        # Merge LawVM-authored source-defect patches — the carrier is now
        # ``_SOURCE_DEFECT_YAML`` (separate from upstream-corrigenda retries,
        # which arrive as per-``stable_id`` overlays on official rows). The
        # rows here ONLY apply to amendments that have no upstream Finlex
        # corrigendum; the file has been curated to exclude upstream
        # retries (which moved to ``corrigendum_retry_overlays_fi.jsonl``).
        manual_path = _SOURCE_DEFECT_YAML
        if manual_path.exists():
            try:
                entries = yaml.safe_load(manual_path.read_text(encoding="utf-8")) or []
                manual_seen: set[str] = set()
                for i, entry in enumerate(entries):
                    amendment_id_numyr = str(entry.get("amendment_id", "")).strip()
                    wrong = str(entry.get("wrong_text", "")).strip()
                    correct = str(entry.get("correct_text", "")).strip()
                    corr_type = str(entry.get("correction_type", "johtolause")).strip()
                    if not (amendment_id_numyr and wrong and correct and wrong != correct):
                        continue
                    amendment_id = _to_grafter_mid(amendment_id_numyr)
                    if amendment_id is None:
                        continue
                    # Apply-cardinality invariant: patches that target exactly
                    # one byte span leave this at the default 1; table-row or
                    # repeat-pattern fixes opt in to N>1 explicitly. Loader-
                    # tolerant — missing field default 1 preserves all current
                    # patch behaviour.
                    try:
                        eac = int(entry.get("expected_apply_count") or 1)
                    except (TypeError, ValueError):
                        eac = 1
                    if eac < 1:
                        eac = 1
                    wrong_norm = _WHITESPACE_RE.sub(" ", _normalize_ws(_ell_re.sub("…", wrong))).strip()
                    correct_norm = _WHITESPACE_RE.sub(" ", _normalize_ws(_ell_re.sub("…", correct))).strip()
                    # body_text corrections go to a separate dict — not johtolause
                    if corr_type == "body_text":
                        if _contains_same_text_family(
                            amendment_id,
                            corr_type,
                            wrong_norm,
                            correct_norm,
                        ):
                            continue
                        if amendment_id not in table._body_patches:
                            table._body_patches[amendment_id] = []
                        table._body_patches[amendment_id].append((wrong, correct, ""))
                        _kept_text_pairs.setdefault(amendment_id, []).append(
                            (corr_type, wrong_norm, correct_norm)
                        )
                        continue
                    # First manual entry for this amendment clears DB-loaded ops.
                    # Safe because amendments in this file have no upstream
                    # corrigendum (no DB ops to wipe) — the pop is a no-op.
                    if amendment_id not in manual_seen:
                        manual_seen.add(amendment_id)
                        table._patches.pop(amendment_id, None)
                    op = _corrigendum_text_replace_op(
                        op_id=f"manual/{amendment_id_numyr}/{i}",
                        sequence=i,
                        target=_location_to_address("johtolause", corr_type),
                        wrong_text=wrong,
                        correct_text=correct,
                        source=OperationSource(
                            statute_id=f"corr/{amendment_id_numyr}",
                            raw_text=entry.get("notes", ""),
                            corrected_by=amendment_id_numyr,
                            expected_apply_count=eac,
                        ),
                    )
                    if amendment_id not in table._patches:
                        table._patches[amendment_id] = []
                    table._patches[amendment_id].append(op)
            except (OSError, yaml.YAMLError) as exc:
                # Unreadable or malformed source-defect file — DB patches still
                # apply, but the failure must remain visible.
                _record_misapplied(
                    op_id="corr/source_defect_yaml/load",
                    amendment_id="source_defect_yaml",
                    statute_id="source_defect_fixes_fi.yaml",
                    reason="FINLAND.CORRIGENDUM_SOURCE_DEFECT_YAML_LOAD_FAILED",
                    surface="source_defect",
                    wrong_text="",
                    correct_text="",
                    path=str(manual_path),
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                    fallback="db_only",
                )

        table._loaded = True
        return table

    @classmethod
    def load_from_db(cls, db_path: Optional[Path] = None) -> "CorrigendumPatchTable":
        """Legacy compatibility wrapper.

        The authoritative source is now the repo text corpus. If callers pass a
        sqlite path explicitly, the shared loader can still read it.
        """
        return cls.load_from_source(db_path)

    def patch_source_xml(
        self, xml_bytes: bytes, amendment_mid: str
    ) -> Tuple[bytes, List[str]]:
        """Apply corrigendum patches to amendment XML before PEG parsing.

        amendment_mid: YEAR/NUM format (as used in grafter, e.g. "2018/984").
        Returns (patched_bytes, applied_op_ids).
        If no patches for this amendment, returns (xml_bytes, []).

        Patches are restricted to the johtolause byte range — the same elements
        that get_johtolause() reads.  For laki amendments the johtolause is in the
        <preamble> block[substitutions|repeals|insertions] elements.  For asetus
        amendments it falls back to sec_1.  Searching the full XML risks matching
        body sections and silently corrupting amendment text (Opus: net negative).
        """
        ops = self._patches.get(amendment_mid)
        if not ops:
            return xml_bytes, []

        frag_start, frag_end = _johtolause_byte_range(xml_bytes)
        if frag_start < 0:
            return xml_bytes, []

        fragment = xml_bytes[frag_start:frag_end]
        applied: List[str] = []
        statute_id = self._amendment_to_statute.get(amendment_mid, "")
        for op in ops:
            patch = op.text_patch
            wrong_text = patch.selector.match_text if patch is not None else ""
            correct_text = patch.replacement if patch is not None else ""
            if not wrong_text or not correct_text:
                continue
            context_patch = _strip_paired_context_ellipsis_patch(wrong_text, correct_text)
            if context_patch is not None:
                wrong_text, correct_text = context_patch
            wrong_b = wrong_text.encode("utf-8")
            count = fragment.count(wrong_b)
            expected_apply_count = max(1, op.source.expected_apply_count) if op.source else 1
            if count > expected_apply_count:
                _record_misapplied(
                    op_id=op.op_id,
                    amendment_id=amendment_mid,
                    statute_id=statute_id,
                    reason="ambiguous",
                    surface=_surface_from_op_id(op.op_id),
                    count=count,
                    expected_apply_count=expected_apply_count,
                    wrong_text=wrong_text,
                    correct_text=correct_text,
                )
                continue
            new_frag, ok = _apply_text_replace_single_text_slot(
                fragment, wrong_text, correct_text
            )
            if not ok:
                new_frag, ok = _apply_visible_text_delta_single_slot(
                    fragment, wrong_text, correct_text
                )
            if not ok:
                new_frag, ok = _apply_visible_text_delta_multi_slot(
                    fragment, wrong_text, correct_text
                )
            if not ok:
                new_frag, ok = _apply_text_replace(fragment, wrong_text, correct_text)
            if not ok:
                expanded = _expand_single_ellipsis_text_patch(fragment, wrong_text, correct_text)
                if expanded is not None:
                    new_frag, ok = _apply_text_replace(fragment, expanded[0], expanded[1])
            if ok:
                candidate_xml = xml_bytes[:frag_start] + new_frag + xml_bytes[frag_end:]
                try:
                    etree.fromstring(candidate_xml)
                except etree.XMLSyntaxError as exc:
                    repaired_frag, repaired_ok = _apply_text_replace_single_text_slot(
                        fragment, wrong_text, correct_text
                    )
                    if not repaired_ok:
                        repaired_frag, repaired_ok = _apply_visible_text_delta_single_slot(
                            fragment, wrong_text, correct_text
                        )
                    if not repaired_ok:
                        repaired_frag, repaired_ok = _apply_visible_text_delta_multi_slot(
                            fragment, wrong_text, correct_text
                        )
                    if not repaired_ok:
                        _record_misapplied(
                            op_id=op.op_id,
                            amendment_id=amendment_mid,
                            statute_id=statute_id,
                            reason="post_patch_xml_invalid",
                            surface=_surface_from_op_id(op.op_id),
                            wrong_text=wrong_text,
                            correct_text=correct_text,
                            error=str(exc),
                        )
                        continue
                    candidate_xml = xml_bytes[:frag_start] + repaired_frag + xml_bytes[frag_end:]
                    try:
                        etree.fromstring(candidate_xml)
                    except etree.XMLSyntaxError as repaired_exc:
                        _record_misapplied(
                            op_id=op.op_id,
                            amendment_id=amendment_mid,
                            statute_id=statute_id,
                            reason="post_patch_xml_invalid",
                            surface=_surface_from_op_id(op.op_id),
                            wrong_text=wrong_text,
                            correct_text=correct_text,
                            error=str(repaired_exc),
                        )
                        continue
                    new_frag = repaired_frag
                fragment = new_frag
                applied.append(op.op_id)
            else:
                frag_ws = re.sub(r"\s+", " ", _normalize_ws(fragment.decode("utf-8", errors="replace")))
                correct_ws = re.sub(r"\s+", " ", _normalize_ws(correct_text)).strip()
                if correct_ws and correct_ws in frag_ws:
                    _record_misapplied(
                        op_id=op.op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="already_applied",
                        surface=_surface_from_op_id(op.op_id),
                        wrong_text=wrong_text,
                        correct_text=correct_text,
                    )
                else:
                    _record_misapplied(
                        op_id=op.op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="miss",
                        surface=_surface_from_op_id(op.op_id),
                        wrong_text=wrong_text,
                        correct_text=correct_text,
                    )

        if applied:
            xml_bytes = xml_bytes[:frag_start] + fragment + xml_bytes[frag_end:]

        return xml_bytes, applied

    def patch_source_body_xml(
        self, xml_bytes: bytes, amendment_mid: str
    ) -> Tuple[bytes, List[str]]:
        """Apply body-text patches to amendment XML before section extraction.

        Used for SD amendments whose body content is truncated/incomplete
        (source pathology — Finlex publication error, not a johtolause issue).
        The patches are applied to the full <body>...</body> byte range.

        Returns (patched_bytes, applied_op_ids).
        """
        body_patch_list = self._body_patches.get(amendment_mid)
        if not body_patch_list:
            return xml_bytes, []

        body_start = xml_bytes.find(b"<body>")
        if body_start < 0:
            return xml_bytes, []
        body_end_tag = xml_bytes.rfind(b"</body>")
        if body_end_tag < 0:
            return xml_bytes, []
        body_end = body_end_tag + len(b"</body>")

        fragment = xml_bytes[body_start:body_end]
        applied: List[str] = []
        statute_id = self._amendment_to_statute.get(amendment_mid, "")
        for idx, (wrong, correct, location_desc) in enumerate(body_patch_list):
            op_id = f"body_patch/{amendment_mid}/{idx}"
            wrong_b = wrong.encode("utf-8")
            count = fragment.count(wrong_b)
            # Body patches today are (wrong, correct, location) tuples; the
            # expected_apply_count carrier is wired through OperationSource
            # for johtolause patches only. Body patches use the default N=1
            # cardinality invariant here (over-applied → ambiguous). Extending
            # the body-patch tuple to carry expected_apply_count is a
            # follow-up; for current data every body patch is N=1.
            expected_apply_count = 1
            if count > expected_apply_count:
                _record_misapplied(
                    op_id=op_id,
                    amendment_id=amendment_mid,
                    statute_id=statute_id,
                    reason="ambiguous",
                    surface=_surface_from_op_id(op_id),
                    count=count,
                    expected_apply_count=expected_apply_count,
                    wrong_text=wrong,
                    correct_text=correct,
                )
                continue
            new_frag, ok = _apply_text_replace(fragment, wrong, correct)
            if ok:
                # Prefer the minimal visible-text delta so source XML spacing
                # and transport quirks outside the actual correction are
                # preserved, but only after the full-body witness matched.
                local_frag, local_ok = _apply_text_replace_single_text_slot(fragment, wrong, correct)
                if local_ok:
                    new_frag = local_frag
            if not ok:
                new_frag, ok = _apply_bare_section_num_corrigendum(
                    fragment, wrong, correct, location_desc
                )
            if not ok:
                sec, subsec = _parse_location_section(location_desc) if location_desc else (None, None)
                if sec is not None:
                    _record_misapplied(
                        op_id=op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="FINLAND.CORRIGENDUM_BODY_LOCATION_FALLBACK_BLOCKED",
                        wrong_text=wrong,
                        correct_text=correct,
                        location_desc=location_desc,
                        section=sec,
                        subsection=subsec or "",
                        scope="section_element",
                    )
                    continue
            if ok:
                candidate_xml = xml_bytes[:body_start] + new_frag + xml_bytes[body_end:]
                try:
                    etree.fromstring(candidate_xml)
                except etree.XMLSyntaxError as exc:
                    repaired_frag, repaired_ok = _apply_visible_text_delta_single_slot(
                        fragment, wrong, correct
                    )
                    if not repaired_ok:
                        repaired_frag, repaired_ok = _apply_section_num_heading_corrigendum(
                            fragment, wrong, correct
                        )
                    if repaired_ok:
                        candidate_xml = xml_bytes[:body_start] + repaired_frag + xml_bytes[body_end:]
                        try:
                            etree.fromstring(candidate_xml)
                        except etree.XMLSyntaxError as repaired_exc:
                            _record_misapplied(
                                op_id=op_id,
                                amendment_id=amendment_mid,
                                statute_id=statute_id,
                                reason="post_patch_xml_invalid",
                                wrong_text=wrong,
                                correct_text=correct,
                                error=str(repaired_exc),
                            )
                            continue
                        new_frag = repaired_frag
                        fragment = new_frag
                        applied.append(op_id)
                        continue
                    _record_misapplied(
                        op_id=op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="post_patch_xml_invalid",
                        wrong_text=wrong,
                        correct_text=correct,
                        error=str(exc),
                    )
                    continue
                fragment = new_frag
                applied.append(op_id)
            else:
                frag_ws = re.sub(r"\s+", " ", _normalize_ws(fragment.decode("utf-8", errors="replace")))
                correct_ws = re.sub(r"\s+", " ", _normalize_ws(correct)).strip()
                if correct_ws and correct_ws in frag_ws:
                    _record_misapplied(
                        op_id=op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="already_applied",
                        surface=_surface_from_op_id(op_id),
                        wrong_text=wrong,
                        correct_text=correct,
                    )
                else:
                    _record_misapplied(
                        op_id=op_id,
                        amendment_id=amendment_mid,
                        statute_id=statute_id,
                        reason="miss",
                        surface=_surface_from_op_id(op_id),
                        wrong_text=wrong,
                        correct_text=correct,
                    )

        if applied:
            xml_bytes = xml_bytes[:body_start] + fragment + xml_bytes[body_end:]

        return xml_bytes, applied


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, safe for asyncio single-threaded use)
# ---------------------------------------------------------------------------

_PATCH_TABLE: Optional[CorrigendumPatchTable] = None

# Module-level accumulator for misapplied/ambiguous corrigendum ops.
# Populated by patch_source_xml / patch_source_body_xml during replay.
# Callers (bench, corpus build) can flush this to disk via
# flush_misapplied_records() for feedback-loop diagnostics.
_MISAPPLIED: list[dict[str, Any]] = []

# Closed vocabulary of misapply reasons. Per AGENTS.md §1.9 (typed carriers
# over dynamic shape), this is the typed enumeration of every reason a
# corrigendum/source-defect/retry patch can fail to apply cleanly.
# Add a new value here ONLY if it is documented, tested, and observable in
# the misapplied ledger. The enum replaces the prior unbounded ``reason``
# string; the runtime assertion in ``_record_misapplied`` keeps the list
# closed as new emission sites appear.
MISAPPLY_REASONS: frozenset[str] = frozenset(
    {
        # Apply-path outcomes.
        "miss",
        "ambiguous",
        "already_applied",
        # Sub-class of already_applied: wrong_text has zero occurrences AND
        # the corrigendum's correct_text IS present in source XML — meaning
        # the source XML LawVM acquired has the corrigendum's effect already
        # applied (acquisition returned a post-corrigendum version, or the
        # consolidated text carried the correction). This is an "honest
        # acceptance" residual — the patch's effect is realised, but the
        # runtime patch itself could not fire.
        "already_applied_in_source",
        "post_patch_xml_invalid",
        # Typed pathology reasons (with stable FINLAND.* namespaces).
        "FINLAND.CORRIGENDUM_BODY_LOCATION_FALLBACK_BLOCKED",
        "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED",
        "FINLAND.INLINE_CORRIGENDUM_MISSING_AUTHORIAL_NOTE",
        "FINLAND.INLINE_CORRIGENDUM_MISSING_WRONG_TEXT",
        "FINLAND.CORRIGENDUM_ADD_EMPTY_BODY",
        "FINLAND.CORRIGENDUM_ADD_UNSUPPORTED",
        # Carrier-load failures (YAML / overlay JSONL).
        "FINLAND.CORRIGENDUM_SOURCE_DEFECT_YAML_LOAD_FAILED",
        "FINLAND.CORRIGENDUM_RETRY_OVERLAY_PARSE_FAILED",
        "FINLAND.CORRIGENDUM_RETRY_OVERLAY_MISSING_STABLE_ID",
        "FINLAND.CORRIGENDUM_RETRY_OVERLAY_EMPTY_PATCHES",
        "FINLAND.CORRIGENDUM_RETRY_OVERLAY_DUPLICATE_TARGET",
        "FINLAND.CORRIGENDUM_EFFECTIVE_DATE_NOT_YET_REACHED",
        # Closed-set self-enforcement: emitting these means a caller
        # passed an unrecognised reason/surface; the record still lands.
        "FINLAND.MISAPPLY_REASON_NOT_IN_CLOSED_SET",
        "FINLAND.MISAPPLY_SURFACE_NOT_IN_CLOSED_SET",
    }
)

# Per-source-surface tag for misapplied records. Distinguishes which carrier
# produced this misapply, so a per-surface rate can be reported and an
# adjudicated-but-explained miss can be filtered honestly. Aligned with the
# three-surface topology: (a) upstream-corrigendum + retry-overlay, (c)
# source-defect, (b) oracle-override (reserved).
MISAPPLY_SURFACES: frozenset[str] = frozenset(
    {
        "upstream_corrigendum",
        "retry_overlay",
        "source_defect",
        "oracle_override",  # reserved — surface (b), step 6
    }
)


def _surface_from_op_id(op_id: str) -> str:
    """Infer the carrier surface (a/b/c) from a patch op_id prefix.

    Used to tag misapply records with which surface produced the failure.
    The op_id prefix is set at op-construction time in
    ``CorrigendumPatchTable.load_from_source``. Today's prefixes:

      ``corr/``   → upstream_corrigendum  (LLM-extracted official-row patch)
      ``retry/``  → retry_overlay          (per-stable_id overlay patch)
      ``manual/`` → source_defect          (source_defect_fixes_fi.yaml row)
      ``body_patch/`` → upstream_corrigendum default (tuples don't carry
                      surface; the source-defect body_text routing is a
                      known follow-up — step 4's typed carrier addition
                      defers to the legacy 3-tuple form today).
    """
    if op_id.startswith("retry/"):
        return "retry_overlay"
    if op_id.startswith("manual/"):
        return "source_defect"
    return "upstream_corrigendum"


def _record_misapplied(
    *,
    op_id: str,
    amendment_id: str,
    statute_id: str,
    reason: str,
    wrong_text: str,
    correct_text: str,
    surface: str = "upstream_corrigendum",
    **extra: object,
) -> None:
    # Closed-enum invariant: a new reason must be added to ``MISAPPLY_REASONS``
    # so the diagnostic surface stays typed. A violation emits a
    # ``FINLAND.MISAPPLY_REASON_NOT_IN_CLOSED_SET`` diagnostic that propagates
    # through the same ledger (§1.10 fail-loud) without silently dropping the
    # record.
    if reason not in MISAPPLY_REASONS:
        _MISAPPLIED.append(
            {
                "op_id": op_id,
                "amendment_id": amendment_id,
                "statute_id": statute_id,
                "reason": "FINLAND.MISAPPLY_REASON_NOT_IN_CLOSED_SET",
                "surface": surface if surface in MISAPPLY_SURFACES else "upstream_corrigendum",
                "wrong_text": wrong_text,
                "correct_text": correct_text,
                "unrecognised_reason": reason,
                **extra,
            }
        )
        return
    if surface not in MISAPPLY_SURFACES:
        # Surface drift smells like a carrier-type leak — surface the
        # diagnostic rather than silently tagging with the default.
        _MISAPPLIED.append(
            {
                "op_id": op_id,
                "amendment_id": amendment_id,
                "statute_id": statute_id,
                "reason": "FINLAND.MISAPPLY_SURFACE_NOT_IN_CLOSED_SET",
                "surface": "upstream_corrigendum",
                "wrong_text": wrong_text,
                "correct_text": correct_text,
                "unrecognised_surface": surface,
                **extra,
            }
        )
        return
    _MISAPPLIED.append(
        {
            "op_id": op_id,
            "amendment_id": amendment_id,
            "statute_id": statute_id,
            "reason": reason,
            "surface": surface,
            "wrong_text": wrong_text,
            "correct_text": correct_text,
            **extra,
        }
    )


def get_patch_table(db_path: Optional[Path] = None) -> CorrigendumPatchTable:
    """Return the module-level CorrigendumPatchTable, loading on first call."""
    global _PATCH_TABLE
    if _PATCH_TABLE is None:
        _PATCH_TABLE = CorrigendumPatchTable.load_from_source(db_path)
    return _PATCH_TABLE


def reset_patch_table() -> None:
    """Reset the singleton (for testing)."""
    global _PATCH_TABLE
    _PATCH_TABLE = None


def get_misapplied_records() -> list[dict[str, Any]]:
    """Return accumulated misapplied corrigendum records (miss + ambiguous)."""
    return list(_MISAPPLIED)


def clear_misapplied_records() -> None:
    """Clear the accumulator (e.g. between bench runs)."""
    _MISAPPLIED.clear()


def flush_misapplied_records(path: Optional[Path] = None) -> Optional[Path]:
    """Write accumulated misapplied records to JSONL and clear the accumulator.

    Returns the path written, or None if nothing to write.
    """
    if not _MISAPPLIED:
        return None
    import json as _json
    target = Path(path) if path is not None else (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data" / "finland" / "corrigendum_misapplied_fi.jsonl"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for rec in _MISAPPLIED:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    _MISAPPLIED.clear()
    return target
