"""Finnish statute cross-reference extractor — Phase 8.3b.

Extracts cross-reference graph edges from Finnish statute XML (Akoma Ntoso).
Each statute cites other statutes via inline `ref` elements in the body text
and via metadata relationships (repeals, issuedUnderActs).

Entry point:

  extract_cross_refs(xml_bytes, statute_id) -> List[CrossRefEdge]
      All typed cross-reference edges FROM statute_id to other statutes.

Edge types (CrossRefEdge.edge_type):
  CITES        — inline reference in body text (inline `ref` element)
  REPEALS      — this statute repeals target (finlex:repeals metadata)
  ISSUED_UNDER — this statute was issued under authority of target
  ISSUES       — this statute has issued a decree under its own authority (target)

Source-of-truth: Finlex Akoma Ntoso consolidated XML in the corpus store.
Patterns ported from earlier local graph prototypes on 2026-03-22.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from dataclasses import dataclass
from typing import List, Optional

from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    classify_eu_instrument_type,
    recognize_celex,
    recognize_eu_acts,
    recognize_eu_year_first_slash,
)
from lawvm.finland.references.sections import (
    coordinated_member_paths_from_ref_surface,
)
from lawvm.core.quirks_disposition import QuirksDisposition

_AKN_NS = 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0'
_FX_NS  = 'http://data.finlex.fi/schema/finlex'

_NS = {
    'akn':    _AKN_NS,
    'finlex': _FX_NS,
}

# Match /akn/fi/act/statute[-consolidated]/YEAR/NUMBER[#provision-path]
_REF_PATTERN = re.compile(
    r'/akn/fi/act/statute(?:-consolidated)?/(\d{4})/(\d+(?:-\d+)?)'
    r'(?:[^#]*#([a-z0-9_/~.-]+))?'  # optional: #provision-path (e.g. #sec_12)
)

# Match /akn/fi/doc/government-proposal/YEAR/NUMBER — Finlex typed HE backlinks
# used in <hcontainer name="preliminaryWork"> ("Esityöt") sections of
# consolidated statutes. Captures the statute→HE lineage edge:
# "this consolidated act came from HE N/Y." Target statute_id is formed as
# "he/YEAR/NUMBER" to distinguish from /akn/fi/act/statute targets.
_HE_REF_PATTERN = re.compile(
    r'/akn/fi/doc/government-proposal/(\d{4})/(\d+(?:-\d+)?)'
)

# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class CrossRefEdge:
    """A cross-reference edge from one Finnish statute to another.

    All edges are directed: source_statute_id → target_statute_id.

    edge_type values:
      CITES        — inline `ref` element in body text
      REPEALS      — source repeals target (finlex:repeals metadata)
      ISSUED_UNDER — source issued under authority of target (finlex:issuedUnderActs)
      ISSUES       — source has issued a decree as target (finlex:issuedUnderThisAct)
    """
    source_statute_id: str
    target_statute_id: str
    edge_type: str           # CITES | REPEALS | ISSUED_UNDER | ISSUES
    source_section: str = ""  # provision address in source, e.g. "12" (if parseable)
    target_section: str = ""  # provision address in target, e.g. "sec_4" (raw AKN path)
    count: int = 1            # for CITES: how many times this target is cited in source
    target_stat_hash: str = ""  # SHA256[:16] of target's consolidated XML at build time
                                # empty if target not in consolidated corpus
                                # enables stale-ref detection: rebuild and compare
    surface_text: str = ""    # literal <ref> text when available; empty for metadata edges
    edge_subtype: str = ""    # refines edge_type for sub-roles, e.g. "REPEALS_EMBEDDED"
                              # for an EU act named only as repealed provenance inside a
                              # long-form citation. Empty = use edge_type as the subtype.
    # Provenance back to the source bytes. For CITES edges derived from an inline
    # AKN <ref> element, this is the BYTE offset/length of the <ref>…</ref>
    # element inside the source statute's `xml_bytes` (the first occurrence when
    # an aggregated edge represents several identical citations; `count` records
    # the total). UNIT: bytes into `xml_bytes`. None for metadata edges
    # (REPEALS / ISSUED_UNDER / ISSUES) which have no body surface span, and for
    # any CITES edge whose surface could not be located in the raw bytes.
    source_byte_offset: Optional[int] = None
    source_byte_len: int = 0
    target_kind: str = ""     # drafting KIND of an ISSUED_UNDER target authority basis,
                              # carried from the AuthorityEdge that produced the edge:
                              # "act" (laki), "decree" (asetus), "decision" (päätös), or
                              # "" (unknown / legacy). Read by the reference-mention lift
                              # so a laki basis types as a statute cross-reference instead
                              # of a non-statutory instrument. Empty = legacy/instrument.


@dataclass(frozen=True)
class CrossRefDiagnostic:
    """Typed extraction diagnostic for cross-reference edges not emitted."""

    rule_id: str
    family: str
    phase: str
    source_statute_id: str
    reason: str
    edge_type: str = ""
    href: str = ""
    target_statute_id: str = ""
    source_section: str = ""
    target_section: str = ""
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "phase": self.phase,
            "source_statute_id": self.source_statute_id,
            "reason": self.reason,
            "edge_type": self.edge_type,
            "href": self.href,
            "target_statute_id": self.target_statute_id,
            "source_section": self.source_section,
            "target_section": self.target_section,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_statute_id(year: str, num_raw: str) -> str:
    """Build canonical statute_id from AKN path components.

    Modern: '1535' → '1992/1535'
    Old with sub-number: '39-001' → '1889/39-001'
    """
    if '-' in num_raw:
        return f"{year}/{num_raw}"
    try:
        return f"{year}/{int(num_raw)}"
    except ValueError:
        return f"{year}/{num_raw}"


def _parse_ref_href(href: str) -> Optional[tuple[str, str]]:
    """Parse an AKN ref href → (statute_id, provision_path) or None.

    Handles two AKN URI families:
    - /akn/fi/act/statute[-consolidated]/YEAR/NUMBER — enacted statute targets
    - /akn/fi/doc/government-proposal/YEAR/NUMBER — HE backlinks in
      Finlex preliminaryWork ("Esityöt") sections; target_statute_id
      is namespaced as "he/YEAR/NUMBER" to distinguish from enacted refs.
    """
    m = _REF_PATTERN.match(href)
    if m:
        return (_make_statute_id(m.group(1), m.group(2)), m.group(3) or "")
    m = _HE_REF_PATTERN.match(href)
    if m:
        return (f"he/{m.group(1)}/{int(m.group(2))}", "")
    return None


def _body_byte_range(xml_bytes: bytes) -> tuple[int, int]:
    """Byte range ``[lo, hi)`` of the AKN ``<body>`` element in the raw bytes.

    The Finlex envelope places a ``<references>`` block and a
    ``<proprietary>``/``<finlex:ref>`` metadata block BEFORE the body, and the
    same citation ``href`` often appears in that metadata first. Scoping the
    href search to the body range stops the locator from latching onto a
    metadata occurrence (the multi-KB-span catastrophe). Falls back to the whole
    document if no ``<body`` open tag is found (defensive — body-less inputs
    have no inline ``<ref>`` to locate anyway).
    """
    lo = xml_bytes.find(b"<body")
    if lo < 0:
        return (0, len(xml_bytes))
    close = xml_bytes.rfind(b"</body>")
    hi = close + len(b"</body>") if close >= 0 else len(xml_bytes)
    return (lo, hi)


def _find_ref_start_tag_end(xml_bytes: bytes, attr_pos: int) -> Optional[int]:
    """Byte just after the ``>`` of the ``<ref …>`` start tag enclosing ``attr_pos``.

    Matches ``<ref`` only when immediately followed by a space or ``>`` so it can
    never latch onto ``<references`` (of which ``<ref`` is a prefix). Returns
    ``None`` if no genuine ``<ref`` start tag precedes ``attr_pos`` or the tag's
    closing ``>`` cannot be found.
    """
    cursor = attr_pos
    while True:
        cand = xml_bytes.rfind(b"<ref", 0, cursor)
        if cand < 0:
            return None
        nxt = xml_bytes[cand + 4:cand + 5]
        if nxt in (b" ", b">"):
            gt = xml_bytes.find(b">", cand)
            if gt < 0:
                return None
            return gt + 1
        # ``<ref`` was a prefix of a longer tag (e.g. ``<references``); keep
        # walking left for a real ``<ref`` start tag.
        cursor = cand


def _locate_ref_byte_span(
    xml_bytes: bytes,
    href: str,
    *,
    search_from: int = 0,
    body_lo: int = 0,
    body_hi: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    """Locate the byte span of an inline ``<ref href="HREF">…</ref>`` element.

    ElementTree discards source positions, so we recover the span by locating
    the element's ``href`` attribute in the raw bytes and expanding to the
    enclosing ``<ref`` start tag and matching ``</ref>`` close tag.

    The returned span covers ONLY the inner citation phrase — the bytes between
    the start tag's ``>`` and the ``</ref>`` close — so ``xml_bytes[off:off+len]``
    slices exactly the surface text, not the ``<ref href="…">…</ref>`` markup
    envelope.

    UNIT: bytes into ``xml_bytes``. Returns ``(byte_offset, byte_len)``, or
    ``None`` if it cannot be located (e.g. the href contained characters that
    were re-encoded by the XML parser). The href search is scoped to
    ``[body_lo, body_hi)`` so a duplicate href inside the leading
    ``<references>``/``<proprietary>`` metadata block cannot be matched. The
    search starts at ``max(search_from, body_lo)`` so repeated identical hrefs
    can be walked left-to-right by advancing the cursor.
    """
    if not href:
        return None
    if body_hi is None:
        body_hi = len(xml_bytes)
    start_from = max(search_from, body_lo)
    # The href appears verbatim as an attribute value: href="HREF" or href='HREF'.
    needle_dq = b'href="' + href.encode("utf-8") + b'"'
    needle_sq = b"href='" + href.encode("utf-8") + b"'"
    attr_pos = xml_bytes.find(needle_dq, start_from, body_hi)
    if attr_pos < 0:
        attr_pos = xml_bytes.find(needle_sq, start_from, body_hi)
    if attr_pos < 0:
        return None
    # Expand left to the enclosing genuine "<ref " / "<ref>" start tag and take
    # the byte just after its closing ">" — the start of the inner phrase.
    inner_start = _find_ref_start_tag_end(xml_bytes, attr_pos)
    if inner_start is None:
        return None
    # Expand right to the matching "</ref>" close — the inner phrase ends there.
    close = xml_bytes.find(b"</ref>", inner_start)
    if close < 0:
        return None
    return (inner_start, close - inner_start)


def _find_section_ancestor(
    elem: ET.Element[str],
    parent_map: dict[ET.Element[str], ET.Element[str]],
) -> str:
    """Return the num text of the nearest AKN section ancestor, or ''."""
    current = parent_map.get(elem)
    while current is not None:
        tag_local = current.tag.split('}')[-1] if '}' in current.tag else current.tag
        if tag_local == 'section':
            num_el = current.find(f'{{{_AKN_NS}}}num')
            if num_el is not None and num_el.text:
                return num_el.text.strip().rstrip('§').strip()
            return ''
        current = parent_map.get(current)
    return ''


@dataclass(frozen=True)
class AnnotationRefRecord:
    """A RAW body ``<ref>`` annotation surface — a WITNESS, not an asserted edge.

    This is the faithful 1:1 surface of a single inline AKN ``<ref>`` element in
    the statute body: the href as written, the href-resolved target (statute_id +
    provision path, when the href parses), the displayed citation text, and the
    byte span of the inner phrase. It performs NO interpretation beyond href
    parsing — no coordinated-member expansion, no dedup, no self-reference
    filtering, no aggregation. (The production cross-reference path in
    :func:`extract_cross_refs` does all of that to build asserted graph edges;
    this iterator deliberately does NOT, so the witness layer can compare the
    grammar-induced reference set against the *unmodified* annotation surface.)

    ``target_statute_id`` / ``target_section`` are empty when the href does not
    parse to a known AKN URI family (``parsed_ok=False``) — the annotation is
    still recorded as a witness (an unparseable href is itself a fact), never
    silently dropped.
    """

    href: str
    target_statute_id: str          # "" when href did not parse
    target_section: str             # raw AKN provision path, "" if none / unparsed
    displayed_text: str             # whitespace-collapsed inner citation phrase
    source_section: str             # nearest section-ancestor num text, "" if none
    source_byte_offset: Optional[int]  # byte offset of inner phrase into xml_bytes
    source_byte_len: int
    parsed_ok: bool                 # True iff the href resolved to a target


def iter_body_annotation_refs(xml_bytes: bytes) -> List[AnnotationRefRecord]:
    """Yield every inline body ``<ref>`` element as a raw annotation witness.

    The annotation-witness surface (grammar7 §13-A): the ``<ref>`` markup as it
    is, with NO production interpretation. Each body ``<ref>`` becomes exactly one
    :class:`AnnotationRefRecord` (including self-references and unparseable hrefs)
    so the witness count equals the literal ``<ref>``-element count in the body.

    Reuses the same byte-span / section-ancestor / href-parse helpers the
    production cross-reference path uses, scoped to the ``<body>`` range so a
    duplicate href in the leading metadata block is never matched. Returns ``[]``
    on XML parse error or a body-less document (fail-soft: no body, no inline
    ``<ref>`` annotations to witness).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    body = root.find('akn:act/akn:body', _NS)
    if body is None:
        body = root.find(f'{{{_AKN_NS}}}body')
    if body is None:
        body = root.find('.//body')
    if body is None:
        return []

    body_lo, body_hi = _body_byte_range(xml_bytes)
    parent_map = {child: parent for parent in root.iter() for child in parent}
    href_search_cursor: dict[str, int] = {}

    out: List[AnnotationRefRecord] = []
    for ref_elem in body.iter(f'{{{_AKN_NS}}}ref'):
        href = ref_elem.get('href', '')
        displayed = " ".join("".join(ref_elem.itertext()).split())
        parsed = _parse_ref_href(href)
        b_off: Optional[int] = None
        b_len = 0
        if href:
            located = _locate_ref_byte_span(
                xml_bytes, href,
                search_from=href_search_cursor.get(href, 0),
                body_lo=body_lo, body_hi=body_hi,
            )
            if located is not None:
                b_off, b_len = located
                href_search_cursor[href] = b_off + 1
        if parsed is not None:
            target_id, prov_path = parsed
            out.append(AnnotationRefRecord(
                href=href,
                target_statute_id=target_id,
                target_section=prov_path,
                displayed_text=displayed,
                source_section=_find_section_ancestor(ref_elem, parent_map),
                source_byte_offset=b_off,
                source_byte_len=b_len,
                parsed_ok=True,
            ))
        else:
            out.append(AnnotationRefRecord(
                href=href,
                target_statute_id="",
                target_section="",
                displayed_text=displayed,
                source_section=_find_section_ancestor(ref_elem, parent_map),
                source_byte_offset=b_off,
                source_byte_len=b_len,
                parsed_ok=False,
            ))
    return out


def _record_self_reference_skip(
    diagnostics_out: Optional[list[CrossRefDiagnostic]],
    *,
    statute_id: str,
    edge_type: str,
    href: str = "",
    source_section: str = "",
    target_section: str = "",
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        CrossRefDiagnostic(
            rule_id="fi_cross_ref_self_reference_skipped",
            family="graph_edge_filter",
            phase="cross_ref_extraction",
            source_statute_id=statute_id,
            reason="Finnish cross-reference extractor skipped a self-reference edge.",
            edge_type=edge_type,
            href=href,
            target_statute_id=statute_id,
            source_section=source_section,
            target_section=target_section,
            blocking=False,
            strict_disposition="record",
        )
    )


def _refs_from(
    root: ET.Element[str],
    xpath: str,
    *,
    source_statute_id: str = "",
    edge_type: str = "",
    diagnostics_out: Optional[list[CrossRefDiagnostic]] = None,
) -> List[str]:
    """Collect all statute IDs referenced under the given XPath (within finlex namespace)."""
    results = []
    for ref_elem in root.findall(xpath, _NS):
        href = ref_elem.get('href', '')
        parsed = _parse_ref_href(href)
        if parsed:
            target_id, prov_path = parsed
            if source_statute_id and target_id == source_statute_id:
                _record_self_reference_skip(
                    diagnostics_out,
                    statute_id=source_statute_id,
                    edge_type=edge_type,
                    href=href,
                    target_section=prov_path,
                )
                continue
            results.append(target_id)
    return results


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_cross_refs(
    xml_bytes: bytes,
    statute_id: str,
    *,
    diagnostics_out: Optional[list[CrossRefDiagnostic]] = None,
    authority_xml_bytes: Optional[bytes] = None,
) -> List[CrossRefEdge]:
    """Extract all cross-reference edges from a Finnish statute XML.

    Produces edges for: inline body citations, repeals, issued_under, and
    decrees issued under this statute's authority.

    Self-references are skipped because the graph edge would be reflexive. If
    ``diagnostics_out`` is provided, each skipped self-reference is recorded as
    ``fi_cross_ref_self_reference_skipped``.

    Args:
        xml_bytes:  Raw XML bytes of the statute (Akoma Ntoso / Finlex format).
            Inline body citations, repeals, and issued_under METADATA edges are
            read from these bytes (typically the consolidated/oracle XML).
        statute_id: Canonical statute ID of the SOURCE, e.g. "2009/953".
        authority_xml_bytes: Raw XML bytes to parse for the preamble "N §:n
            nojalla" authority-basis clause that supplies the ISSUED_UNDER
            section + drafting kind. Defaults to ``xml_bytes``. Callers that hold
            the BASE (unconsolidated) statute XML should pass it here: Finlex
            drops the preamble from the consolidated form of older statutes, so
            the nojalla clause survives only in the base XML. Passing the base
            keeps the section/kind merge working where the consolidated XML alone
            would yield nothing.

    Returns:
        List of CrossRefEdge instances. Multiple CITES edges to the same target
        are deduplicated and their count is aggregated.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        if diagnostics_out is not None:
            diagnostics_out.append(
                CrossRefDiagnostic(
                    rule_id="fi_cross_ref_xml_parse_failed",
                    family="source_pathology",
                    phase="cross_ref_extraction",
                    source_statute_id=statute_id,
                    reason="Finnish cross-reference extraction skipped source XML because parsing failed.",
                    blocking=True,
                    strict_disposition="block",
                )
            )
        return []

    edges: List[CrossRefEdge] = []

    # ── CITES: inline body refs ──────────────────────────────────────────────
    body = root.find('akn:act/akn:body', _NS)
    if body is None: body = root.find(f'{{{_AKN_NS}}}body')
    if body is None:
        # Try without namespace prefix (some documents omit it)
        body = root.find('.//body')

    # Phase 9.0: provision-level CITES — one edge per (src_sec, target_id, tgt_sec) triple.
    # (src_sec, target_id, prov_path) -> (count, surface_text). If repeated
    # refs for the same target use different text, the surface is ambiguous and
    # left empty rather than pretending one spelling owns all occurrences.
    # First-occurrence byte span per aggregated key (UNIT: bytes into xml_bytes).
    cite_spans: dict[tuple[str, str, str], tuple[int, int]] = {}
    # Per-href cursor so repeated identical hrefs are located left-to-right and
    # the first occurrence of each aggregated key wins its byte span.
    href_search_cursor: dict[str, int] = {}
    cite_counts: dict[tuple[str, str, str], tuple[int, str]] = {}
    if body is not None:
        # Scope inline-ref byte-span recovery to the <body> range so a duplicate
        # href inside the leading <references>/<proprietary> metadata block can
        # never be matched (that mismatch produced multi-KB spans).
        body_lo, body_hi = _body_byte_range(xml_bytes)
        parent_map = {child: parent for parent in root.iter() for child in parent}
        for ref_elem in body.iter(f'{{{_AKN_NS}}}ref'):
            href = ref_elem.get('href', '')
            parsed = _parse_ref_href(href)
            if parsed:
                target_id, prov_path = parsed
                if target_id != statute_id:
                    src_sec = _find_section_ancestor(ref_elem, parent_map)
                    key = (src_sec, target_id, prov_path)
                    surface = " ".join("".join(ref_elem.itertext()).split())
                    # Locate this occurrence's byte span, advancing the cursor so
                    # a later identical href maps to the next element in the bytes.
                    located = _locate_ref_byte_span(
                        xml_bytes, href,
                        search_from=href_search_cursor.get(href, 0),
                        body_lo=body_lo, body_hi=body_hi,
                    )
                    if located is not None:
                        b_off, b_len = located
                        href_search_cursor[href] = b_off + 1
                        # First occurrence of this aggregated key keeps its span.
                        if key not in cite_spans:
                            cite_spans[key] = (b_off, b_len)
                    prev_count, prev_surface = cite_counts.get(key, (0, surface))
                    if prev_surface != surface:
                        prev_surface = ""
                    cite_counts[key] = (prev_count + 1, prev_surface)
                else:
                    _record_self_reference_skip(
                        diagnostics_out,
                        statute_id=statute_id,
                        edge_type="CITES",
                        href=href,
                        source_section=_find_section_ancestor(ref_elem, parent_map),
                        target_section=prov_path,
                    )

    # The set of CITES (src_sec, target_id, prov_path) triples actually emitted,
    # so a coordinated-member addition below never duplicates an edge that some
    # other <ref> already produced for that same member.
    emitted_cite_keys: set[tuple[str, str, str]] = set(cite_counts.keys())
    for (src_sec, target_id, prov_path), (count, surface_text) in cite_counts.items():
        span = cite_spans.get((src_sec, target_id, prov_path))
        b_off = span[0] if span is not None else None
        b_len = span[1] if span is not None else 0
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='CITES',
            source_section=src_sec,
            target_section=prov_path,
            count=count,
            surface_text=surface_text,
            source_byte_offset=b_off,
            source_byte_len=b_len,
        ))
        # A Finlex inline <ref>'s href anchors only the FIRST member of a
        # coordinated section list ("(360/1968) 18 a ja 18 b §:ssä" → sec_18a),
        # but the LawVM convention enumerates EVERY coordinated member. Re-parse
        # the ref's own surface text through the shared body recognizer and emit a
        # section-level CITES edge for each coordinated sibling the href dropped.
        # Skipped when the aggregated surface is ambiguous (empty) — without a
        # single trusted spelling there is no coordination text to expand.
        if not surface_text:
            continue
        for extra_path in coordinated_member_paths_from_ref_surface(
            surface_text, prov_path
        ):
            extra_key = (src_sec, target_id, extra_path)
            if extra_key in emitted_cite_keys:
                continue
            emitted_cite_keys.add(extra_key)
            edges.append(CrossRefEdge(
                source_statute_id=statute_id,
                target_statute_id=target_id,
                edge_type='CITES',
                source_section=src_sec,
                target_section=extra_path,
                count=count,
                surface_text=surface_text,
                source_byte_offset=b_off,
                source_byte_len=b_len,
            ))

    # ── REPEALS: this statute repeals target ─────────────────────────────────
    for target_id in _refs_from(
        root,
        './/finlex:repeals//finlex:ref',
        source_statute_id=statute_id,
        edge_type="REPEALS",
        diagnostics_out=diagnostics_out,
    ):
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='REPEALS',
        ))

    # ── ISSUED_UNDER: this statute was issued under authority of target ───────
    for target_id in _refs_from(
        root,
        './/finlex:issuedUnderActs//finlex:ref',
        source_statute_id=statute_id,
        edge_type="ISSUED_UNDER",
        diagnostics_out=diagnostics_out,
    ):
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='ISSUED_UNDER',
        ))

    # ── ISSUES: this statute has issued decrees (target) ─────────────────────
    for target_id in _refs_from(
        root,
        './/finlex:issuedUnderThisAct//finlex:ref',
        source_statute_id=statute_id,
        edge_type="ISSUES",
        diagnostics_out=diagnostics_out,
    ):
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='ISSUES',
        ))

    # ── ISSUED_UNDER enrichment: section + drafting-kind from the preamble ────
    # The finlex:issuedUnderActs metadata names the authority basis but carries
    # neither the cited section nor whether the basis is a laki / asetus / päätös.
    # Both live in the preamble "N §:n nojalla" clause. Merge the AuthorityEdge
    # facts here so EVERY projection that calls extract_cross_refs (lawvm cite,
    # the surface-graph reference lens via ref_mention_extractor, the StatuteGraph
    # builders) sees the same section + target_kind — the single source of truth
    # for the nojalla authority-basis typing. (Previously this merge lived only in
    # build_statute_graph_fi_lightweight, leaving cite + the lens on the old
    # untyped, sectionless edges.)
    _merge_authority_basis(
        edges,
        authority_xml_bytes if authority_xml_bytes is not None else xml_bytes,
        statute_id,
    )

    return edges


def _merge_authority_basis(
    edges: List[CrossRefEdge],
    xml_bytes: bytes,
    statute_id: str,
) -> None:
    """Enrich ISSUED_UNDER edges with the preamble "nojalla" section + kind.

    Parses the statute preamble for the Finnish "N §:n nojalla" authority-basis
    construction (via ``extract_asetus_authority``) and, in place:

    * populates ``target_section`` (the cited section(s), e.g. "60a") on each
      existing ISSUED_UNDER edge whose target appears as a nojalla basis;
    * sets ``target_kind`` ("act" / "decree" / "decision") from the per-basis
      drafting inflection — so a laki basis types as a statute cross-reference
      while a genuine decree/decision basis stays a non-statutory instrument;
    * appends ISSUED_UNDER edges for nojalla bases that are absent from the
      finlex:issuedUnderActs metadata (which is sometimes incomplete).

    The kind is recorded for every basis (even sectionless ones); it is NOT
    blanket-set to "act" — ~6% of bases are genuinely decree/decision.
    """
    from collections import defaultdict

    from lawvm.finland.delegation import extract_asetus_authority

    if not xml_bytes:
        return
    auth_edges = extract_asetus_authority(xml_bytes, statute_id).accepted_items
    if not auth_edges:
        return

    # parent_statute_id → ordered cited sections; and → first recognizable kind.
    auth_map: dict[str, list[str]] = defaultdict(list)
    parent_kind: dict[str, str] = {}
    for ae in auth_edges:
        if ae.parent_section:
            auth_map[ae.parent_statute_id].append(ae.parent_section)
        # First recognizable kind for a basis wins; register the basis even when
        # its kind is empty, but don't clobber a known kind with a later empty one.
        parent_kind.setdefault(ae.parent_statute_id, "")
        if ae.parent_kind and not parent_kind[ae.parent_statute_id]:
            parent_kind[ae.parent_statute_id] = ae.parent_kind

    # Update existing ISSUED_UNDER edges with section info + authority kind.
    existing_targets: set[str] = set()
    for edge in edges:
        if edge.edge_type == "ISSUED_UNDER":
            existing_targets.add(edge.target_statute_id)
            if edge.target_statute_id in auth_map:
                secs = auth_map[edge.target_statute_id]
                edge.target_section = ",".join(dict.fromkeys(secs))  # dedup, keep order
            kind = parent_kind.get(edge.target_statute_id, "")
            if kind:
                edge.target_kind = kind

    # Add ISSUED_UNDER edges found in the preamble but absent from metadata.
    for parent_id, secs in auth_map.items():
        if parent_id not in existing_targets:
            edges.append(CrossRefEdge(
                source_statute_id=statute_id,
                target_statute_id=parent_id,
                edge_type="ISSUED_UNDER",
                target_section=",".join(dict.fromkeys(secs)),
                target_kind=parent_kind.get(parent_id, ""),
            ))


# ---------------------------------------------------------------------------
# Johtolause amendment-target (<affectedDocument>) references
# ---------------------------------------------------------------------------
#
# Every amending statute names the act it amends in the preamble enacting clause
# (``<formula name="enactingClause">``) via one or more AKN ``<affectedDocument
# href="/akn/fi/act/statute/YEAR/NUMBER">`` elements — the johtolause
# "muutetaan … annetun … asetuksen (NNN/YYYY) …" / "kumotaan … lain (NNN/YYYY)"
# construction. This is the single most important cross-statute link in an
# amending statute, yet it lives OUTSIDE ``<body>`` and so is never seen by the
# inline-``<ref>`` body scan in ``extract_cross_refs`` (which scopes to the body
# range). Pure-amendment statutes — whose entire substance is the johtolause plus
# quoted replacement text — therefore surface ZERO cross-statute references.
#
# This extractor scans the ``<affectedDocument>`` elements and emits one
# AMENDS-typed edge per distinct amendment target. AMENDS is the surface
# reference/entity for the amendment relation; the replay/apply engine already
# knows the amendment target independently from the same johtolause, so this is
# purely the surface link, not a replay input.

# Match the inner ``<affectedDocument …>`` start tag (followed by a space or
# ``>``) so it cannot latch onto a longer tag that merely shares the prefix.
_AFFECTED_DOC_START = re.compile(rb"<(?:[A-Za-z0-9_]+:)?affectedDocument(?=[\s>])")
_AFFECTED_DOC_CLOSE = re.compile(rb"</(?:[A-Za-z0-9_]+:)?affectedDocument>")


def _locate_affected_document_span(
    xml_bytes: bytes,
    href: str,
    *,
    search_from: int = 0,
) -> Optional[tuple[int, int]]:
    """Locate the inner-phrase byte span of an ``<affectedDocument href=HREF>``.

    Mirrors :func:`_locate_ref_byte_span` but for ``<affectedDocument>``: returns
    the span of the bytes BETWEEN the start tag's ``>`` and the matching
    ``</affectedDocument>`` close — the displayed citation phrase ("1129/2014"),
    not the markup envelope. ``None`` if it cannot be located. The search starts
    at ``search_from`` so repeated identical hrefs are walked left-to-right.
    """
    if not href:
        return None
    needle_dq = b'href="' + href.encode("utf-8") + b'"'
    needle_sq = b"href='" + href.encode("utf-8") + b"'"
    attr_pos = xml_bytes.find(needle_dq, search_from)
    if attr_pos < 0:
        attr_pos = xml_bytes.find(needle_sq, search_from)
    if attr_pos < 0:
        return None
    start_match = None
    for m in _AFFECTED_DOC_START.finditer(xml_bytes, 0, attr_pos + 1):
        start_match = m
    if start_match is None:
        return None
    gt = xml_bytes.find(b">", attr_pos)
    if gt < 0:
        return None
    inner_start = gt + 1
    close = _AFFECTED_DOC_CLOSE.search(xml_bytes, inner_start)
    if close is None:
        return None
    return (inner_start, close.start() - inner_start)


def extract_affected_document_refs(
    xml_bytes: bytes,
    statute_id: str,
    *,
    diagnostics_out: Optional[list[CrossRefDiagnostic]] = None,
) -> List[CrossRefEdge]:
    """Extract johtolause amendment-target edges from ``<affectedDocument>``.

    Scans the preamble ``<formula name="enactingClause">`` for AKN
    ``<affectedDocument href="/akn/fi/act/statute/YEAR/NUMBER">`` elements and
    emits one ``AMENDS`` edge per distinct amendment target. The edge carries the
    canonical target id (via :func:`_parse_ref_href`), the displayed citation
    surface, and the inner-phrase byte span into ``xml_bytes``.

    Self-references are skipped (an amending statute never lists itself as its own
    amendment target; a recorded self-reference would be a source pathology) and,
    when ``diagnostics_out`` is provided, recorded as
    ``fi_cross_ref_self_reference_skipped``.

    Targets are deduplicated: a statute may repeat the same ``<affectedDocument>``
    across several enacting-clause blocks (kumotaan / muutetaan / lisätään), but
    that is one amendment relation → one edge. ``count`` records the number of
    ``<affectedDocument>`` occurrences; the byte span anchors the first.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # Parse failure is already reported by extract_cross_refs; stay silent
        # here rather than double-reporting the same pathology.
        return []

    # The element lives under <preamble><formula name="enactingClause">; scan the
    # whole document for robustness (it appears nowhere else in the envelope).
    target_count: dict[str, int] = {}
    target_surface: dict[str, str] = {}
    target_span: dict[str, tuple[int, int]] = {}
    href_search_cursor: dict[str, int] = {}
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local != "affectedDocument":
            continue
        href = el.get("href", "")
        parsed = _parse_ref_href(href)
        if not parsed:
            continue
        target_id, _prov_path = parsed
        if target_id == statute_id:
            _record_self_reference_skip(
                diagnostics_out,
                statute_id=statute_id,
                edge_type="AMENDS",
                href=href,
            )
            continue
        surface = " ".join("".join(el.itertext()).split())
        located = _locate_affected_document_span(
            xml_bytes, href, search_from=href_search_cursor.get(href, 0)
        )
        if located is not None:
            href_search_cursor[href] = located[0] + 1
            target_span.setdefault(target_id, located)
        target_count[target_id] = target_count.get(target_id, 0) + 1
        target_surface.setdefault(target_id, surface)

    edges: List[CrossRefEdge] = []
    for target_id, count in target_count.items():
        span = target_span.get(target_id)
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type="AMENDS",
            count=count,
            surface_text=target_surface.get(target_id, ""),
            source_byte_offset=span[0] if span is not None else None,
            source_byte_len=span[1] if span is not None else 0,
        ))
    return edges


# ---------------------------------------------------------------------------
# Phase 10.3: EU cross-jurisdiction references
# ---------------------------------------------------------------------------

# Finnish text patterns for EU legislation citations:
#   "(EY) N:o 999/2001"     — old number-first form
#   "(EU) 2016/679"         — modern year-first form
#   "999/2001/EY"           — alternative order
#   CELEX "32016R0679"      — legislative history
# Recognition is shared with the preparatory-reference lane via
# references.eu_reference (DIALECT_CROSS_REF preserves this lane's exact
# patterns: EU|EY|ETY|EURATOM|ETA, re.I, plus the NUMBER/YEAR/FORM order).
# Lowering into CrossRefEdge (type classification + sanity filter + dedup)
# stays here; the instrument-TYPE discrimination (regulation/directive/decision)
# is delegated to the shared, M1-backed ``classify_eu_instrument_type`` so the
# gradated heads (``asetuksen``, ``päätöksen``) classify soundly instead of
# falling through a nominative substring test to the generic 'act'.
_EU_JURISDICTION = re.compile(r'\b(EU|EY|ETY|EURATOM|ETA)\b')

_CELEX_TYPE = {'R': 'reg', 'L': 'dir', 'D': 'dec'}

# The year-first slash form "YEAR/NUMBER/FORM" ("2001/23/EY" in "Neuvoston
# direktiivi 2001/23/EY") is recognised via the shared
# ``recognize_eu_year_first_slash(DIALECT_CROSS_REF)`` waist (the same shape the
# eu_directive lane consumes), de-duplicating the recogniser the two lanes used
# to keep in lockstep. The shared ``recognize_eu_acts`` NUMBER/YEAR/FORM pattern
# requires a 4-digit MIDDLE group, so it only reads the number-first order; this
# fills the year-first gap. Year bounds are enforced by the _add sanity filter.

# Look-behind: 40 chars before a match to detect the act type keyword
_TYPE_LOOKBEHIND = 40


def _classify_eu_type(text: str, match_start: int) -> str:
    """Classify regulation/directive/decision from text before the match.

    Delegates to the shared, M1-backed
    :func:`~lawvm.finland.references.eu_reference.classify_eu_instrument_type`:
    a look-behind token whose TAIL is an M1-generated EU-instrument-head surface
    (``asetuksen`` → reg, ``päätöksen`` → dec, ``direktiivin`` → dir) classifies
    that token's type. The head closest to the match wins; absent any head the
    type is the generic ``act``. This is paradigm inversion over a closed head
    set, not an ``asetu`` substring guess — so gradated forms classify correctly.
    """
    window = text[max(0, match_start - _TYPE_LOOKBEHIND):match_start]
    return classify_eu_instrument_type(window, default='act')


def _normalize_eu_year(year: str) -> str:
    """Expand a (possibly 2-digit) EU-act year fragment to a 4-digit year.

    A 4-digit fragment is returned verbatim. A 2-digit fragment ``yy`` is
    expanded by the codebase century pivot (same rule as the plain-text Finnish
    statute-id lane): ``yy <= current 2-digit year`` → ``20yy``, otherwise
    ``19yy``. EU-act numbering runs from the 1950s to present, so the window is
    unambiguous in practice (e.g. ``91`` → ``1991``, ``02`` → ``2002``).
    """
    if len(year) != 2:
        return year
    yy = int(year)
    current_yy = date.today().year % 100
    century = 2000 if yy <= current_yy else 1900
    return str(century + yy)


def _eu_statute_id(eu_type: str, year: str, number: str) -> str:
    """Canonical cross-jurisdiction statute ID: 'eu/reg/2016/679'."""
    try:
        num = str(int(number))
    except ValueError:
        num = number
    return f"eu/{eu_type}/{year}/{num}"


def extract_eu_refs(xml_bytes: bytes, statute_id: str) -> List[CrossRefEdge]:
    """Extract EU cross-jurisdiction references from a Finnish statute XML.

    Returns CrossRefEdge instances where target_statute_id follows the
    canonical form 'eu/TYPE/YEAR/NUMBER' (e.g. 'eu/reg/2016/679').

    edge_type is always 'CITES' (EU law references are always textual cites).
    source_section is the AKN section label if detectable.

    Pattern coverage:
      - "(EU|EY|ETY) N:o NUMBER/YEAR"  — old number-first form
      - "(EU) YEAR/NUMBER"              — modern year-first form (GDPR-style, e.g. 2016/679)
      - "NUMBER/YEAR/EU|EY"             — alternative notation
      - CELEX numbers "3YYYYRNNNN"      — legislative history notes

    Args:
        xml_bytes:  Raw XML bytes of the Finnish statute.
        statute_id: Canonical Finnish statute ID of the source.

    Returns:
        List of CrossRefEdge instances (deduplicated by (src_sec, target_id)).
    """
    try:
        text = xml_bytes.decode('utf-8', errors='replace')
    except UnicodeDecodeError:
        # errors="replace" makes this unreachable in practice, but guard defensively.
        return []

    # Key on (src_sec, target_id, edge_subtype) so an act cited both as a primary
    # target and as embedded-repeal provenance (rare) stays two distinct edges.
    seen: dict[tuple[str, str, str], int] = {}  # (src_sec, target_id, subtype) → count
    # First-occurrence byte span per key (UNIT: bytes into xml_bytes, computed by
    # encoding the decoded-text char prefix up to the recognizer's char start).
    eu_spans: dict[tuple[str, str, str], tuple[int, int]] = {}
    # First-occurrence matched surface per key — the EU citation phrase exactly as
    # it appears in the decoded text (the recognizer's ``raw``, e.g.
    # "(EY) N:o 999/2001"). Verbatim substring of the source text, so it byte-matches
    # for downstream re-anchoring / viewer overlay / provenance. Empty when the
    # surface for the first occurrence was not recorded.
    eu_surfaces: dict[tuple[str, str, str], str] = {}
    # We don't have element-level section context for text patterns;
    # use empty string for source_section (provision-level tracking is not
    # feasible from plain text without full DOM traversal).

    def _add(
        eu_type: str,
        year: str,
        number: str,
        subtype: str,
        *,
        char_start: int = -1,
        char_end: int = -1,
        surface: str = "",
    ) -> None:
        # Expand a 2-digit "(ETY) N:o 2092/91"-style year before sanity-checking
        # and id-building, so legacy EU citations land on the same canonical
        # eu/TYPE/YEAR/NUMBER id as their 4-digit form.
        year = _normalize_eu_year(year)
        if int(year) < 1957 or int(year) > 2050:
            return  # sanity filter
        target_id = _eu_statute_id(eu_type, year, number)
        key = ('', target_id, subtype)
        seen[key] = seen.get(key, 0) + 1
        # Record the byte span of the first occurrence of this aggregated key.
        if key not in eu_spans and char_start >= 0 and char_end > char_start:
            b_off = len(text[:char_start].encode("utf-8"))
            b_len = len(text[char_start:char_end].encode("utf-8"))
            eu_spans[key] = (b_off, b_len)
        # Record the matched surface of the first occurrence (verbatim substring).
        if key not in eu_surfaces and surface:
            eu_surfaces[key] = surface

    # recognize_eu_acts(DIALECT_CROSS_REF) yields all matches across the
    # N:o form, the modern year-first form, and the NUMBER/YEAR/FORM order,
    # in pattern order — identical to the prior P1 → P1B → P2 scan. (The old
    # '(' fast-path guard on P1B was a no-op: that pattern requires '(' to
    # match at all, so dropping the guard does not change the match set.)
    # An EU act tagged role="repealed_embedded" is named only as provenance the
    # outer (enacting) act repeals — typed as a distinct REPEALS_EMBEDDED subtype,
    # separate from the statute's own finlex:repeals metadata edge.
    for ref in recognize_eu_acts(text, dialect=DIALECT_CROSS_REF):
        eu_type = _classify_eu_type(text, ref.start)
        subtype = "REPEALS_EMBEDDED" if ref.role == "repealed_embedded" else ""
        _add(
            eu_type, ref.year, ref.number, subtype,
            char_start=ref.start, char_end=ref.end,
            surface=ref.raw,
        )

    for ref in recognize_celex(text, dialect=DIALECT_CROSS_REF):
        assert ref.celex_type is not None
        eu_type = _CELEX_TYPE.get(ref.celex_type.upper(), 'act')
        _add(
            eu_type, ref.year, str(int(ref.number)), "",
            char_start=ref.start, char_end=ref.end,
            surface=ref.raw,
        )

    # Year-first slash form ("2001/23/EY") that the shared NUMBER/YEAR/FORM
    # recognizer misses because its middle group must be 4 digits. Common in
    # signature/footer citations like "Neuvoston direktiivi 2001/23/EY".
    for ref in recognize_eu_year_first_slash(text, dialect=DIALECT_CROSS_REF):
        eu_type = _classify_eu_type(text, ref.start)
        _add(
            eu_type, ref.year, ref.number, "",
            char_start=ref.start, char_end=ref.end,
            surface=ref.raw,
        )

    edges: List[CrossRefEdge] = []
    for (src_sec, target_id, subtype), count in seen.items():
        span = eu_spans.get((src_sec, target_id, subtype))
        b_off = span[0] if span is not None else None
        b_len = span[1] if span is not None else 0
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='CITES',
            source_section=src_sec,
            target_section='',
            count=count,
            # The matched EU citation surface (verbatim substring of the source
            # text, e.g. "(EY) N:o 999/2001") — drives byte re-anchoring, the
            # viewer overlay, and provenance, exactly like the <ref> lane.
            surface_text=eu_surfaces.get((src_sec, target_id, subtype), ""),
            edge_subtype=subtype,
            source_byte_offset=b_off,
            source_byte_len=b_len,
        ))
    return edges
