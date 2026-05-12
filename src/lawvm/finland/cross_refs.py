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
from dataclasses import dataclass
from typing import List, Optional

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
    quirks_disposition: str = "record"

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


def _parse_ref_href(href: str) -> Optional[tuple]:
    """Parse an AKN ref href → (statute_id, provision_path) or None."""
    m = _REF_PATTERN.match(href)
    if not m:
        return None
    return (_make_statute_id(m.group(1), m.group(2)), m.group(3) or "")


def _find_section_ancestor(elem: ET.Element, parent_map: dict) -> str:
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
    root: ET.Element,
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
) -> List[CrossRefEdge]:
    """Extract all cross-reference edges from a Finnish statute XML.

    Produces edges for: inline body citations, repeals, issued_under, and
    decrees issued under this statute's authority.

    Self-references are skipped because the graph edge would be reflexive. If
    ``diagnostics_out`` is provided, each skipped self-reference is recorded as
    ``fi_cross_ref_self_reference_skipped``.

    Args:
        xml_bytes:  Raw XML bytes of the statute (Akoma Ntoso / Finlex format).
        statute_id: Canonical statute ID of the SOURCE, e.g. "2009/953".

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
    cite_counts: dict[tuple, int] = {}  # (src_sec, target_id, prov_path) → count
    if body is not None:
        parent_map = {child: parent for parent in root.iter() for child in parent}
        for ref_elem in body.iter(f'{{{_AKN_NS}}}ref'):
            href = ref_elem.get('href', '')
            parsed = _parse_ref_href(href)
            if parsed:
                target_id, prov_path = parsed
                if target_id != statute_id:
                    src_sec = _find_section_ancestor(ref_elem, parent_map)
                    key = (src_sec, target_id, prov_path)
                    cite_counts[key] = cite_counts.get(key, 0) + 1
                else:
                    _record_self_reference_skip(
                        diagnostics_out,
                        statute_id=statute_id,
                        edge_type="CITES",
                        href=href,
                        source_section=_find_section_ancestor(ref_elem, parent_map),
                        target_section=prov_path,
                    )

    for (src_sec, target_id, prov_path), count in cite_counts.items():
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='CITES',
            source_section=src_sec,
            target_section=prov_path,
            count=count,
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

    return edges


# ---------------------------------------------------------------------------
# Phase 10.3: EU cross-jurisdiction references
# ---------------------------------------------------------------------------

# Finnish text patterns for EU legislation citations:
#   "(EY) N:o 999/2001" or "(EU) N:o 2016/679"  — most common
#   "999/2001/EY" or "2016/679/EU"               — alternative order
_EU_TYPE_MAP = {
    'asetus':    'reg',    # regulation
    'direktiivi': 'dir',   # directive
    'päätös':    'dec',    # decision
    'asiakirja': 'act',    # generic
}
_EU_JURISDICTION = re.compile(r'\b(EU|EY|ETY|EURATOM|ETA)\b')

# Pattern 1: "(EY|EU|ETY|EURATOM|ETA) N:o NUMBER/YEAR"
_EU_REF_P1 = re.compile(
    r'\((?:EU|EY|ETY|EURATOM|ETA)\)\s*N:o\s+(\d+)/(\d{4})',
    re.I,
)
# Pattern 2: "NUMBER/YEAR/EY|EU|ETY|EURATOM|ETA" (with word boundary)
_EU_REF_P2 = re.compile(
    r'(\d+)/(\d{4})/(?:EU|EY|ETY|EURATOM|ETA)\b',
    re.I,
)
# CELEX number: "3YYYYRNNNN" (R=regulation, L=directive, D=decision)
_EU_REF_CELEX = re.compile(r'\b3(\d{4})(R|L|D)(\d{4})\b')

_CELEX_TYPE = {'R': 'reg', 'L': 'dir', 'D': 'dec'}

# Look-behind: 40 chars before a match to detect the act type keyword
_TYPE_LOOKBEHIND = 40


def _classify_eu_type(text: str, match_start: int) -> str:
    """Guess regulation/directive/decision from text before the match."""
    window = text[max(0, match_start - _TYPE_LOOKBEHIND):match_start].lower()
    for fi_word, eu_type in _EU_TYPE_MAP.items():
        if fi_word in window:
            return eu_type
    return 'act'


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
      - "(EU|EY|ETY) N:o NUMBER/YEAR"  — main body text
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

    seen: dict[tuple, int] = {}  # (src_sec, target_id) → count
    # We don't have element-level section context for text patterns;
    # use empty string for source_section (provision-level tracking is not
    # feasible from plain text without full DOM traversal).

    def _add(eu_type: str, year: str, number: str, start: int) -> None:
        if int(year) < 1957 or int(year) > 2050:
            return  # sanity filter
        target_id = _eu_statute_id(eu_type, year, number)
        key = ('', target_id)
        seen[key] = seen.get(key, 0) + 1

    for m in _EU_REF_P1.finditer(text):
        number, year = m.group(1), m.group(2)
        eu_type = _classify_eu_type(text, m.start())
        _add(eu_type, year, number, m.start())

    for m in _EU_REF_P2.finditer(text):
        number, year = m.group(1), m.group(2)
        eu_type = _classify_eu_type(text, m.start())
        _add(eu_type, year, number, m.start())

    for m in _EU_REF_CELEX.finditer(text):
        year, type_char, num_str = m.group(1), m.group(2), m.group(3)
        eu_type = _CELEX_TYPE.get(type_char.upper(), 'act')
        _add(eu_type, year, str(int(num_str)), m.start())

    edges: List[CrossRefEdge] = []
    for (src_sec, target_id), count in seen.items():
        edges.append(CrossRefEdge(
            source_statute_id=statute_id,
            target_statute_id=target_id,
            edge_type='CITES',
            source_section=src_sec,
            target_section='',
            count=count,
        ))
    return edges
