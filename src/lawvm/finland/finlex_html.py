"""finlex_html.py — Fetch and parse Finlex consolidated HTML oracle pages.

Finlex serves consolidated statute pages as Next.js RSC (React Server Components)
streaming HTML. The section structure is embedded as JSON in inline script blocks.

This module handles:
- Fetching HTML via curl (urllib is blocked by Finlex bot detection)
- Caching in Farchive with locator finlex://html/ajantasa/{year}/{num}
- Parsing RSC JSON using proper JSON parsing (primary path) with regex fallback
- Exposing section count and section label lists for freshness comparison

Rate limiting: 1 req/sec enforced via module-level throttle.

Usage:
    from lawvm.finland.finlex_html import html_section_count, html_section_labels

    count = html_section_count("2002", "738")      # -> int | None
    labels = html_section_labels("2018", "1121")   # -> list[str] | None
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, TypedDict

import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CACHE = Path(
    os.environ.get(
        "LAWVM_FARCHIVE_DB",
        str(Path(__file__).parent.parent.parent.parent / "data" / "finlex.farchive"),
    )
)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Legacy freshness knob kept for explicit/manual refresh paths.
_CACHE_MAX_AGE_HOURS: float = 24.0

# Rate limiting: minimum seconds between live fetches
_MIN_FETCH_INTERVAL: float = 1.0

# ---------------------------------------------------------------------------
# Module-level rate limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple module-level rate limiter — avoids bare global float mutation."""
    last_fetch: float = 0.0


_rate_state = _RateLimiter()


def _rate_limit() -> None:
    """Block until at least _MIN_FETCH_INTERVAL seconds since last fetch."""
    elapsed = time.monotonic() - _rate_state.last_fetch
    if elapsed < _MIN_FETCH_INTERVAL:
        time.sleep(_MIN_FETCH_INTERVAL - elapsed)
    _rate_state.last_fetch = time.monotonic()


# ---------------------------------------------------------------------------
# Cache locator scheme
# ---------------------------------------------------------------------------

def _html_locator(year: str, num: str) -> str:
    """Return canonical Farchive locator for an HTML oracle page."""
    return f"finlex://html/ajantasa/{year}/{num}"


# ---------------------------------------------------------------------------
# HTTP fetch via curl (bypass bot detection)
# ---------------------------------------------------------------------------

def _finlex_html_url(year: str, num: str) -> str:
    """Return the canonical Finlex consolidated statute URL.

    The current URL pattern is /fi/lainsaadanto/{year}/{num}.
    The old /fi/laki/ajantasa/{year}/{year}{num:04d} redirects here with HTTP 308.
    We use the direct URL to avoid the redirect round-trip.
    """
    return f"https://www.finlex.fi/fi/lainsaadanto/{year}/{num}"


def _curl_fetch(url: str) -> bytes | None:
    """Fetch URL via curl subprocess with realistic browser user-agent.

    finlex.fi blocks urllib/requests (Cloudflare bot detection).
    curl with a plausible User-Agent header works reliably.

    Returns raw response bytes, or None on any error.
    """
    result = subprocess.run(
        [
            "curl",
            "-s",           # silent (no progress meter)
            "-f",           # fail fast on HTTP errors (returns exit code 22)
            "--max-time", "30",
            "-H", f"User-Agent: {_USER_AGENT}",
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: fi-FI,fi;q=0.9,en;q=0.8",
            "-H", "Accept-Encoding: gzip, deflate, br",
            "--compressed",  # decompress gzip/br automatically
            url,
        ],
        capture_output=True,
        timeout=45,
    )
    if result.returncode != 0:
        return None
    if not result.stdout:
        return None
    return result.stdout


# ---------------------------------------------------------------------------
# Primary path: RSC JSON parsing via proper JSON parser
# ---------------------------------------------------------------------------

def _escape_ctrl_in_strings(s: str) -> str:
    """Re-escape literal control characters inside JSON string values.

    After unescaping the outer RSC encoding, section body text may contain
    literal newline/tab characters inside JSON string values (e.g. from
    "on kumottu L:lla \\n ...").  json.loads() rejects these because the
    JSON spec requires control characters inside strings to be escaped.
    This function re-escapes them so json.loads() succeeds.

    Only touches characters inside JSON string literals (between unescaped
    double quotes), leaving structural JSON whitespace alone.
    """
    def _escape(m: re.Match[str]) -> str:
        return m.group(0).replace('\n', '\\n').replace('\t', '\\t').replace('\r', '\\r')

    return re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', _escape, s, flags=re.DOTALL)


def _find_rsc_documenttocs_block(text: str) -> str | None:
    """Find the RSC push block that contains documentToCs and return the raw inner string.

    Finlex pages contain many ``self.__next_f.push([1,"..."])`` RSC blocks.
    The one containing ``documentToCs`` holds the Finnish (and Swedish) ToC
    headings.  The Finnish headings are always fully inlined as
    ``fin[3]["headings"]``; the Swedish ToC is a lazy ``$Lxx`` reference.

    Returns the raw escaped inner JSON string (still needs unescaping), or
    None if not found.
    """
    # Find all RSC push([1, ...]) blocks and look for the one with documentToCs.
    # We search for the script block: <script>self.__next_f.push([1,"...")  </script>
    # rather than iterating all 500+ blocks individually.
    marker = 'self.__next_f.push([1,"'
    search_from = 0
    while True:
        push_idx = text.find(marker, search_from)
        if push_idx < 0:
            break
        inner_start = push_idx + len(marker)
        # Find the enclosing </script> tag to bound the block
        script_end = text.find('</script>', inner_start)
        if script_end < 0:
            script_end = len(text)
        block_text = text[inner_start:script_end]
        if 'documentToCs' in block_text:
            # Strip the closing "]) from the block
            # The RSC format ends with: \"]) or \n"])
            inner = block_text.rstrip()
            if inner.endswith('"])'):
                inner = inner[:-3]
            return inner
        search_from = script_end

    return None


def _unescape_rsc_json_string(raw: str) -> str:
    r"""Unescape an RSC-encoded JSON string to plain JSON.

    RSC embeds JSON as a doubly-escaped string inside the push([1,"..."]) call.
    This function applies the standard backslash unescapes:
      \\\"  -> \"  (escaped double quotes become literal double quotes)
      \\\\  -> \\  (escaped backslash becomes literal backslash)
      \\n   -> newline
      \\t   -> tab
      \\r   -> carriage return

    The result is valid-ish JSON, but may contain literal control characters
    inside string values (from legal text with embedded newlines).  Callers
    must run ``_escape_ctrl_in_strings`` before passing to ``json.loads``.
    """
    # Order matters: unescape backslashes first, then the others
    s = raw.replace('\\"', '"').replace('\\\\', '\\')
    s = s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
    return s


def _find_documenttocs_in_rsc(data: Any) -> dict[str, Any] | None:
    """Recursively search parsed RSC JSON for the documentToCs dict."""
    if isinstance(data, dict):
        if 'documentToCs' in data:
            return data['documentToCs']  # type: ignore[return-value]
        for v in data.values():
            result = _find_documenttocs_in_rsc(v)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_documenttocs_in_rsc(item)
            if result is not None:
                return result
    return None


def _extract_text_from_children(children: list[Any]) -> str:
    """Recursively extract concatenated text from a children array."""
    parts: list[str] = []
    for child in children:
        if isinstance(child, dict):
            if 'text' in child:
                parts.append(str(child['text']))
            elif 'children' in child:
                parts.append(_extract_text_from_children(child['children']))
    return ''.join(parts)


def _is_repealed(next_element: dict[str, Any]) -> bool:
    """Check if a section's nextElement indicates it was repealed (kumottu)."""
    children = next_element.get('children', [])
    full_text = _extract_text_from_children(children)
    return 'on kumottu' in full_text or 'har upphävts' in full_text


def _collect_headings_from_rsc(
    headings: list[Any],
    out_sections: list[str],
    out_chapters: list[str],
    seen_ids: set[str],
) -> None:
    """Recursively collect section and chapter labels from parsed RSC headings.

    The RSC headings array uses a two-level structure:
    - top-level entries: chapters (chp_N headingId) and free-standing sections
    - each chapter entry has a ``subHeadings`` array containing its sections

    Both levels are walked.  Chapters contribute to out_chapters; sections
    (headingId starting with sec_ or containing __sec_) to out_sections.
    """
    for h in headings:
        if not isinstance(h, dict):
            continue
        elem = h.get('element', {})
        if not isinstance(elem, dict):
            continue
        heading_id = elem.get('headingId', '')
        children = elem.get('children', [])
        label = children[0].get('text', '') if children and isinstance(children[0], dict) else ''

        if heading_id not in seen_ids:
            seen_ids.add(heading_id)
            if _is_section_heading_id(heading_id):
                out_sections.append(label)
            elif _is_chapter_heading_id(heading_id):
                out_chapters.append(label)

        sub = h.get('subHeadings', [])
        if sub:
            _collect_headings_from_rsc(sub, out_sections, out_chapters, seen_ids)


def _collect_heading_entries_from_rsc(
    headings: list[Any],
    out: list[HeadingEntry],
    seen_ids: set[str],
) -> None:
    """Recursively collect HeadingEntry objects from parsed RSC headings.

    Extracts richer data than the regex-based approach:
    - eId from nextElement.attributes.eId
    - original_version from nextElement.attributes.originalVersion
    - original_version_label from nextElement.attributes.originalVersionLabel
    - is_repealed flag from nextElement children text
    - heading_text from nextElement when tagName == "heading"
    """
    for h in headings:
        if not isinstance(h, dict):
            continue
        elem = h.get('element', {})
        if not isinstance(elem, dict):
            continue
        heading_id = elem.get('headingId', '')
        children = elem.get('children', [])
        label = children[0].get('text', '') if children and isinstance(children[0], dict) else ''
        next_elem = h.get('nextElement', {}) or {}

        if heading_id not in seen_ids:
            seen_ids.add(heading_id)
            attrs = next_elem.get('attributes') or {}
            # attrs can be "$undefined" (a string) — guard against that
            if not isinstance(attrs, dict):
                attrs = {}

            e_id: str | None = attrs.get('eId') or None
            original_version: str | None = attrs.get('originalVersion') or None
            original_version_label: str | None = attrs.get('originalVersionLabel') or None
            repealed = _is_repealed(next_elem)

            # Heading text: present when nextElement tagName == "heading"
            heading_text: str | None = None
            next_tag = next_elem.get('tagName', '')
            if next_tag == 'heading':
                ne_children = next_elem.get('children', [])
                heading_text = _extract_text_from_children(ne_children) or None

            if _is_section_heading_id(heading_id):
                entry = HeadingEntry(
                    heading_id=heading_id,
                    text=label,
                    kind='section',
                    eId=e_id,
                    original_version=original_version,
                    original_version_label=original_version_label,
                    is_repealed=repealed,
                    heading_text=heading_text,
                )
                out.append(entry)
            elif _is_chapter_heading_id(heading_id):
                entry = HeadingEntry(
                    heading_id=heading_id,
                    text=label,
                    kind='chapter',
                    eId=e_id,
                    original_version=original_version,
                    original_version_label=original_version_label,
                    is_repealed=repealed,
                    heading_text=heading_text,
                )
                out.append(entry)

        sub = h.get('subHeadings', [])
        if sub:
            _collect_heading_entries_from_rsc(sub, out, seen_ids)


def _parse_rsc_json_headings(text: str) -> HeadingResult | None:
    """Primary path: parse headings by finding and JSON-parsing the RSC documentToCs block.

    Returns None if:
    - The RSC documentToCs block is not found (page structure unexpected)
    - JSON parsing fails for any reason
    - The fin headings array is missing or empty

    Returns a HeadingResult if parsing succeeds (may have empty lists if
    no recognized headings are present).
    """
    raw_inner = _find_rsc_documenttocs_block(text)
    if raw_inner is None:
        return None

    try:
        unescaped = _unescape_rsc_json_string(raw_inner)
        cleaned = _escape_ctrl_in_strings(unescaped)
        # Strip RSC line prefix (e.g. "2b:") before the JSON value
        colon_idx = cleaned.find(':')
        if colon_idx < 0:
            return None
        json_value = cleaned[colon_idx + 1:]
        data = json.loads(json_value)
    except (json.JSONDecodeError, ValueError):
        return None

    doc_tocs = _find_documenttocs_in_rsc(data)
    if doc_tocs is None:
        return None

    fin = doc_tocs.get('fin')
    if not isinstance(fin, list) or len(fin) < 4:
        return None

    fin_data = fin[3]
    if not isinstance(fin_data, dict):
        return None

    headings = fin_data.get('headings', [])
    if not isinstance(headings, list):
        return None

    sections: list[str] = []
    chapters: list[str] = []
    seen_ids: set[str] = set()
    _collect_headings_from_rsc(headings, sections, chapters, seen_ids)

    return HeadingResult(sections=sections, chapters=chapters)


def _parse_rsc_json_heading_entries(text: str) -> list[HeadingEntry] | None:
    """Primary path: parse heading entries with rich metadata from RSC JSON.

    Returns None if JSON parsing fails.  Returns an empty list if parsing
    succeeds but no recognized headings are found.
    """
    raw_inner = _find_rsc_documenttocs_block(text)
    if raw_inner is None:
        return None

    try:
        unescaped = _unescape_rsc_json_string(raw_inner)
        cleaned = _escape_ctrl_in_strings(unescaped)
        colon_idx = cleaned.find(':')
        if colon_idx < 0:
            return None
        json_value = cleaned[colon_idx + 1:]
        data = json.loads(json_value)
    except (json.JSONDecodeError, ValueError):
        return None

    doc_tocs = _find_documenttocs_in_rsc(data)
    if doc_tocs is None:
        return None

    fin = doc_tocs.get('fin')
    if not isinstance(fin, list) or len(fin) < 4:
        return None

    fin_data = fin[3]
    if not isinstance(fin_data, dict):
        return None

    headings = fin_data.get('headings', [])
    if not isinstance(headings, list):
        return None

    out: list[HeadingEntry] = []
    seen_ids: set[str] = set()
    _collect_heading_entries_from_rsc(headings, out, seen_ids)

    return out


# ---------------------------------------------------------------------------
# Legacy path: RSC regex-based parsing (fallback)
# ---------------------------------------------------------------------------

# Match an isNumHeading entry in the escaped RSC JSON stream.
# Captures: (tagName, heading_text, headingId)
_ENTRY_PATTERN: re.Pattern[str] = re.compile(
    r'\\"tagName\\":\\"([^"\\\\]+)\\",\\"attributes\\":\{\\"isNumHeading\\":\\"true\\"'
    r'[^}]*\},\\"children\\":\[\{\\"text\\":\\"([^"\\\\]+)\\"\}[^\]]*\],'
    r'\\"headingId\\":\\"([^"\\\\]+)\\"'
)


def _find_fi_block_legacy(text: str) -> str | None:
    """Legacy fallback: return the substring containing Finnish headings JSON.

    Finlex serves statute pages as Next.js RSC HTML with section headings
    embedded inside inline ``<script>self.__next_f.push([1,"..."])`` blocks.

    This legacy approach uses string-range searching to scope to the Finnish
    block.  It is kept as a fallback for pages where the primary JSON-based
    extraction fails.

    Two strategies are tried in order:

    1. ``documentToCs`` strategy (current URL scheme /fi/lainsaadanto/):
       Locate ``\\"fin\\":[\\"$\\"`` (start of fin ToC array) and end either at
       ``\\"swe\\":[\\"$\\"`` (start of swe ToC array) or end of the enclosing
       script block, whichever comes first.

       **Known defect**: ``swe`` appears as ``"$Lxx"`` (lazy reference) in the
       ``documentToCs`` block, not as an inlined array.  The SWE_MARKER is
       therefore typically NOT found in the same block, so this strategy
       overshoots into a later RSC block (~block #70) that inlines the Swedish
       ToC as a real array.  The result is that Swedish ghost entries can leak
       into the Finnish label list.  This is the bug that the primary JSON
       path fixes.

    2. Legacy ``lang=fi`` strategy (old URL scheme /fi/laki/ajantasa/):
       Locate ``\\"lang\\":\\"fi\\",\\"headings\\":[`` and extract the enclosing
       ``<script>`` block.

    Returns None if neither strategy finds the expected RSC structure.
    """
    # Strategy 1: documentToCs fin/swe split (current /fi/lainsaadanto/ pages)
    FIN_MARKER = '\\"fin\\":[\\"$\\"'
    SWE_MARKER = '\\"swe\\":[\\"$\\"'
    fi_idx = text.find(FIN_MARKER)
    if fi_idx >= 0:
        swe_idx = text.find(SWE_MARKER, fi_idx)
        if swe_idx > fi_idx:
            return text[fi_idx:swe_idx]
        # No swe block found — return to end of enclosing script block
        script_end = text.find('</script>', fi_idx)
        end = script_end if script_end > fi_idx else len(text)
        return text[fi_idx:end]

    # Strategy 2: legacy lang=fi headings block (old /fi/laki/ajantasa/ pages)
    LEGACY_MARKER = r'\\"lang\\":\\"fi\\",\\"headings\\":\['
    fi_idx = text.find(LEGACY_MARKER)
    if fi_idx < 0:
        return None
    script_start = text.rfind('<script', 0, fi_idx)
    if script_start < 0:
        return None
    script_end = text.find('</script>', fi_idx)
    if script_end < 0:
        script_end = len(text)
    return text[script_start:script_end]


def _is_section_heading_id(heading_id: str) -> bool:
    """True if headingId identifies a section (§), not a chapter or other element."""
    return heading_id.startswith('sec_') or '__sec_' in heading_id


def _is_chapter_heading_id(heading_id: str) -> bool:
    """True if headingId identifies a chapter (luku).

    Matches both bare ``chp_N`` and part-scoped ``part_N__chp_M`` IDs,
    but excludes section-scoped IDs like ``chp_N__sec_M``.
    """
    if '__sec_' in heading_id:
        return False
    return heading_id.startswith('chp_') or '__chp_' in heading_id


def _is_part_heading_id(heading_id: str) -> bool:
    """True if headingId identifies a part (osa)."""
    return heading_id.startswith('part_') and '__' not in heading_id


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class HeadingResult(TypedDict):
    sections: list[str]
    chapters: list[str]


class HeadingEntry(TypedDict, total=False):
    """A single heading entry from the Finlex RSC JSON, preserving document order.

    Required fields (always present):
        heading_id: Raw headingId string (e.g. "chp_3__sec_12").
        text: Display label (e.g. "12 §", "3 luku").
        kind: "section" or "chapter".

    Optional fields (present when using the JSON-based parser):
        eId: Version-stamped element ID from nextElement.attributes.eId
             (e.g. "chp_3__sec_12v20060675").
        original_version: Amendment version stamp from nextElement.attributes.originalVersion
                          (e.g. "@20060675").
        original_version_label: Human-readable amendment label from nextElement.attributes.originalVersionLabel
                                (e.g. "2.9.2005/699").
        is_repealed: True if the section/chapter body contains "on kumottu" or
                     "har upphävts".
        heading_text: Section otsikko text when nextElement.tagName == "heading".
    """
    heading_id: str
    text: str
    kind: str
    eId: str | None
    original_version: str | None
    original_version_label: str | None
    is_repealed: bool
    heading_text: str | None


# ---------------------------------------------------------------------------
# Parsing entry points (primary JSON path + legacy fallback)
# ---------------------------------------------------------------------------

def _collect_seen_ids_from_json(text: str) -> set[str]:
    """Re-run JSON tree walk to collect the set of heading IDs it found.

    Used to seed the seen_ids set for the regex supplement pass so that
    entries already captured by the JSON path are not duplicated.
    """
    raw_inner = _find_rsc_documenttocs_block(text)
    if raw_inner is None:
        return set()
    try:
        unescaped = _unescape_rsc_json_string(raw_inner)
        cleaned = _escape_ctrl_in_strings(unescaped)
        colon_idx = cleaned.find(':')
        if colon_idx < 0:
            return set()
        data = json.loads(cleaned[colon_idx + 1:])
    except (json.JSONDecodeError, ValueError):
        return set()
    doc_tocs = _find_documenttocs_in_rsc(data)
    if doc_tocs is None:
        return set()
    fin = doc_tocs.get('fin')
    if not isinstance(fin, list) or len(fin) < 4:
        return set()
    fin_data = fin[3]
    if not isinstance(fin_data, dict):
        return set()
    headings = fin_data.get('headings', [])
    if not isinstance(headings, list):
        return set()
    seen: set[str] = set()
    sections_dummy: list[str] = []
    chapters_dummy: list[str] = []
    _collect_headings_from_rsc(headings, sections_dummy, chapters_dummy, seen)
    return seen


def _supplement_with_full_page_regex(
    text: str,
    existing_sections: list[str],
    existing_chapters: list[str],
    seen_ids: set[str],
) -> None:
    """Scan entire page with regex to find headings missed by JSON tree walk.

    Large statutes with parts (OSA) use lazy RSC references for deeply nested
    content.  The JSON parser walks the documentToCs tree but cannot resolve
    lazy ``$XX:...`` references, so sections/chapters under lazy-loaded parts
    are missed.  The regex finds them in other RSC push blocks.
    """
    for _tagname, heading_text, heading_id in _ENTRY_PATTERN.findall(text):
        if heading_id in seen_ids:
            continue
        seen_ids.add(heading_id)
        if _is_section_heading_id(heading_id):
            existing_sections.append(heading_text)
        elif _is_chapter_heading_id(heading_id):
            existing_chapters.append(heading_text)


def _parse_html_headings(html_bytes: bytes) -> HeadingResult:
    """Parse RSC JSON in HTML bytes into section and chapter heading lists.

    Tries the primary JSON-based path first, then supplements with a full-page
    regex scan to catch headings from lazy-loaded RSC blocks (e.g. deeply
    nested parts in large statutes).  Falls back entirely to the legacy
    regex-based path if JSON parsing fails.

    Returns empty lists if neither path succeeds (bot-block page or
    unexpected structure).
    """
    text = html_bytes.decode("utf-8", errors="replace")

    # Primary: JSON-based extraction
    json_result = _parse_rsc_json_headings(text)
    if json_result is not None and (json_result["sections"] or json_result["chapters"]):
        # Supplement: full-page regex catches lazy-loaded RSC entries
        seen_ids = _collect_seen_ids_from_json(text)
        _supplement_with_full_page_regex(
            text, json_result["sections"], json_result["chapters"], seen_ids,
        )
        return json_result

    # Fallback: full-page regex extraction
    sections: list[str] = []
    chapters: list[str] = []
    seen_ids_fb: set[str] = set()
    _supplement_with_full_page_regex(text, sections, chapters, seen_ids_fb)

    if sections or chapters:
        return HeadingResult(sections=sections, chapters=chapters)

    return HeadingResult(sections=[], chapters=[])


def _parse_html_heading_entries(html_bytes: bytes) -> list[HeadingEntry]:
    """Parse RSC JSON in HTML bytes into an ordered list of heading entries.

    Tries the primary JSON-based path first.  Falls back to the legacy
    regex-based path if JSON parsing fails.

    Unlike ``_parse_html_headings``, this preserves the document order of
    both chapter and section headings, and carries the raw ``headingId`` for
    chapter-membership detection (``chp_N__sec_M`` vs bare ``sec_M``).

    The JSON path additionally provides eId, original_version,
    original_version_label, is_repealed, and heading_text fields.

    Returns an empty list if neither path finds the expected structure.
    """
    text = html_bytes.decode("utf-8", errors="replace")

    # Primary: JSON-based extraction
    json_entries = _parse_rsc_json_heading_entries(text)
    if json_entries is not None and json_entries:
        # Supplement with full-page regex for lazy-loaded RSC entries
        seen_ids = {e['heading_id'] for e in json_entries}
        for _tagname, heading_text, heading_id in _ENTRY_PATTERN.findall(text):
            if heading_id in seen_ids:
                continue
            seen_ids.add(heading_id)
            if _is_section_heading_id(heading_id):
                json_entries.append(HeadingEntry(heading_id=heading_id, text=heading_text, kind="section"))
            elif _is_chapter_heading_id(heading_id):
                json_entries.append(HeadingEntry(heading_id=heading_id, text=heading_text, kind="chapter"))
        return json_entries

    # Fallback: full-page regex extraction (no rich metadata)
    entries: list[HeadingEntry] = []
    seen_ids_fb: set[str] = set()

    for _tagname, heading_text, heading_id in _ENTRY_PATTERN.findall(text):
        if heading_id in seen_ids_fb:
            continue
        seen_ids_fb.add(heading_id)
        if _is_section_heading_id(heading_id):
            entries.append(HeadingEntry(heading_id=heading_id, text=heading_text, kind="section"))
        elif _is_chapter_heading_id(heading_id):
            entries.append(HeadingEntry(heading_id=heading_id, text=heading_text, kind="chapter"))

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_html_oracle(
    year: str,
    num: str,
    cache_path: str | Path | None = None,
    max_age_hours: float = _CACHE_MAX_AGE_HOURS,
    force_refresh: bool = False,
) -> bytes | None:
    """Return cached consolidated HTML or explicitly refresh it from finlex.fi.

    URL pattern: https://www.finlex.fi/fi/lainsaadanto/{year}/{num}

    Uses curl with a realistic User-Agent to bypass Finlex bot detection.
    Ordinary calls are cache-only. Live refresh happens only when
    ``force_refresh=True`` is passed explicitly.

    Args:
        year: Statute year as string (e.g. "2002").
        num:  Statute number as string (e.g. "738").
        cache_path: Path to Farchive DB. Defaults to data/finlex.farchive
                    or LAWVM_FARCHIVE_DB env var.
        max_age_hours: Legacy TTL knob used only by explicit/manual refresh paths.
        force_refresh: If True, fetch live and overwrite cache.

    Returns:
        Raw HTML bytes, or None on any fetch/cache error.
    """
    from farchive import Farchive
    db_path = Path(cache_path) if cache_path is not None else _DEFAULT_CACHE
    archive = Farchive(db_path)
    locator = _html_locator(year, num)
    url = _finlex_html_url(year, num)

    cached = archive.get(locator)
    if not force_refresh:
        return cached

    # Live fetch — enforce rate limit
    _rate_limit()
    html = _curl_fetch(url)
    if html is None:
        return cached

    # Store in archive and return
    archive.store(locator, html, storage_class="html")
    return html


def html_section_count(
    year: str,
    num: str,
    cache_path: str | Path | None = None,
    max_age_hours: float = _CACHE_MAX_AGE_HOURS,
    force_refresh: bool = False,
) -> int | None:
    """Count sections (§) in the consolidated Finlex HTML oracle.

    Fetches and parses the RSC JSON headings block. Returns the number of
    section headings (entries with headingId matching sec_N or chp_X__sec_N).

    Args:
        year: Statute year as string.
        num:  Statute number as string.
        cache_path: Path to Farchive DB.
        max_age_hours: Cache TTL for the fetched HTML.
        force_refresh: Bypass cache if True.

    Returns:
        Section count (int >= 0), or None if the page could not be fetched
        or does not contain the expected RSC structure.
    """
    html = fetch_html_oracle(
        year, num,
        cache_path=cache_path,
        max_age_hours=max_age_hours,
        force_refresh=force_refresh,
    )
    if html is None:
        return None

    result = _parse_html_headings(html)
    sections = result["sections"]

    # If we got an HTML response but zero sections, the page may be a bot-block
    # or an old-format page without RSC structure. Return None to signal failure.
    if not sections:
        return None

    return len(sections)


def html_section_labels(
    year: str,
    num: str,
    cache_path: str | Path | None = None,
    max_age_hours: float = _CACHE_MAX_AGE_HOURS,
    force_refresh: bool = False,
) -> list[str] | None:
    """Extract section labels from the consolidated Finlex HTML oracle.

    Returns labels in document order, e.g. ['1 §', '2 §', '2 a §', '3 §', ...].
    More detailed than html_section_count — enables per-section comparison
    against the XML oracle.

    Args:
        year: Statute year as string.
        num:  Statute number as string.
        cache_path: Path to Farchive DB.
        max_age_hours: Cache TTL for the fetched HTML.
        force_refresh: Bypass cache if True.

    Returns:
        List of section label strings, or None if the page could not be fetched
        or does not contain the expected RSC structure.
    """
    html = fetch_html_oracle(
        year, num,
        cache_path=cache_path,
        max_age_hours=max_age_hours,
        force_refresh=force_refresh,
    )
    if html is None:
        return None

    result = _parse_html_headings(html)
    sections = result["sections"]

    if not sections:
        return None

    return sections


def html_heading_entries(
    year: str,
    num: str,
    cache_path: str | Path | None = None,
    force_refresh: bool = False,
) -> list[HeadingEntry] | None:
    """Return ordered heading entries (chapters and sections) for a statute.

    Each entry carries the raw ``headingId`` which encodes chapter membership
    for scoped sections (e.g. ``chp_1__sec_2``), enabling chapter-grouped
    triple-view rendering without a separate chapter pass.

    When the primary JSON path succeeds, entries additionally carry:
    ``eId``, ``original_version``, ``original_version_label``,
    ``is_repealed``, and ``heading_text``.

    Returns None if the page could not be fetched or has no RSC structure.
    """
    html = fetch_html_oracle(
        year, num,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )
    if html is None:
        return None

    entries = _parse_html_heading_entries(html)
    if not entries:
        return None
    return entries


def html_chapter_labels(
    year: str,
    num: str,
    cache_path: str | Path | None = None,
    max_age_hours: float = _CACHE_MAX_AGE_HOURS,
    force_refresh: bool = False,
) -> list[str] | None:
    """Extract chapter labels (luku) from the consolidated Finlex HTML oracle.

    Returns labels in document order, e.g. ['1 luku', '2 luku', ...].
    Returns None if the page could not be fetched or has no RSC structure.
    Returns an empty list for statutes that have sections but no chapters.
    """
    html = fetch_html_oracle(
        year, num,
        cache_path=cache_path,
        max_age_hours=max_age_hours,
        force_refresh=force_refresh,
    )
    if html is None:
        return None

    result = _parse_html_headings(html)

    # Check we actually got a valid RSC page (sections non-empty is the signal)
    if not result["sections"]:
        return None

    return result["chapters"]


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Quick smoke test for two known statutes.

    Usage (from LawVM/ dir):
        uv run python src/lawvm/finland/finlex_html.py
    """
    import sys

    TEST_CASES = [
        # (year, num, description, expected_min_sections)
        ("2002", "738", "Laki sähköisestä asioinnista viranomaistoiminnassa", 20),
        ("2018", "1121", "Elintarvikemarkkinaketjulaki (XML desync case)", 15),
    ]

    all_pass = True
    print(f"{'Statute':<12} {'Sections':>8}  Description")
    print("-" * 70)

    for year, num, desc, min_expected in TEST_CASES:
        count = html_section_count(year, num)
        if count is None:
            status = "FAIL (None returned)"
            all_pass = False
        elif count < min_expected:
            status = f"FAIL (got {count}, expected >={min_expected})"
            all_pass = False
        else:
            status = "PASS"
        sid = f"{year}/{num}"
        print(f"{sid:<12} {str(count) if count is not None else 'None':>8}  {desc[:40]}  [{status}]")

    print()
    if all_pass:
        print("All smoke tests passed.")
    else:
        print("Some smoke tests FAILED.")
        sys.exit(1)
