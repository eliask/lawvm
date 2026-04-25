"""html_section_extractor.py — Extract section headings from Finlex statute HTML pages.

Finlex (finlex.fi) is a Next.js/React SSR application.  The statute pages do NOT
render a traditional server-side HTML DOM with section tags — instead, the full
Table of Contents (ToC) data is embedded as a JSON string inside inline
``<script>self.__next_f.push([1, "..."])`` blocks.

Structure of the embedded JSON:
    The relevant block contains a key pattern:
        "lang":"fi","headings":[...]
    where each entry in the headings array has the form:
        {
          "element": {
            "tagName": "num",
            "attributes": {"isNumHeading": "true"},
            "children": [{"text": "N §"}],
            "headingId": "sec_N"   (or "chp_X__sec_N" for statutes with chapters)
          },
          "subHeadings": [...]   (contains nested section entries for chapter nodes)
        }

    Because the JSON is embedded as a string literal inside another JSON array, all
    double-quotes are backslash-escaped: `\\"isNumHeading\\":\\"true\\"`.

Key observations from HTML inspection:
  - The `isNumHeading` attribute distinguishes structural headings (sections,
    chapters, luku) from inline references to sections in body text.  Body text
    references appear in the rendered statute text area, NOT in the headings block.
  - The `headingId` field encodes element type:
      sec_N          →  section (statute without chapters)
      chp_X__sec_N   →  section nested inside chapter X
      chp_X          →  chapter heading (1 luku, 2 luku, …)
      entryIntoForce_* →  "voimaantulo" amendment note (not a section)
      OT18, etc.     →  miscellaneous structural headings (not sections)
  - The page always has exactly one Finnish headings block (lang=fi) and usually
    one Swedish block (lang=sv) which is identical in structure but with translated
    heading titles.  We extract only the Finnish block.
  - Section text values look like:  "1 §", "2 §", "2 a §", "13 b §", etc.
  - Chapter text values look like:  "1 luku", "2 luku", etc.
  - Sections may appear at the top level of the headings array OR nested inside
    a chapter's subHeadings list.  The regex scans the full fi-block either way.

Usage:
    from scripts.html_section_extractor import extract_sections_from_html

    with open('page.html', 'rb') as f:
        html_bytes = f.read()

    sections = extract_sections_from_html(html_bytes)
    # Returns: ['1 §', '2 §', '2 a §', '3 §', ...]

    # To also get chapters:
    result = extract_headings_from_html(html_bytes)
    # Returns: {'sections': ['1 §', ...], 'chapters': ['1 luku', ...]}
"""

from __future__ import annotations

import re
from typing import TypedDict


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# The Finnish headings block starts with this escaped JSON key pair.
# It is unique in the page (appears exactly once).
_FI_HEADINGS_MARKER: str = r'\\"lang\\":\\"fi\\",\\"headings\\":\['

# Match an isNumHeading entry: captures (tagName, text, headingId).
# The JSON is backslash-escaped, so literal `"` → `\\"` in the HTML source.
# Breakdown:
#   \\"tagName\\":\\"(...)\\",    → tagName field
#   \\"attributes\\":\{           → start of attributes object
#   \\"isNumHeading\\":\\"true\\" → the marker we care about
#   [^}]*\},                      → rest of attributes (tolerates extra fields)
#   \\"children\\":\[             → start of children array
#   \{\\"text\\":\\"(...)\\"\}    → single text child (the heading label)
#   [^\]]*\],                     → rest of children (tolerates extra elements)
#   \\"headingId\\":\\"(...)\\",  → headingId field
_ENTRY_PATTERN: re.Pattern[str] = re.compile(
    r'\\"tagName\\":\\"([^"\\\\]+)\\",\\"attributes\\":\{\\"isNumHeading\\":\\"true\\"'
    r'[^}]*\},\\"children\\":\[\{\\"text\\":\\"([^"\\\\]+)\\"\}[^\]]*\],'
    r'\\"headingId\\":\\"([^"\\\\]+)\\"'
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_fi_block(text: str) -> str | None:
    """Return the content of the <script> tag containing the Finnish headings.

    Returns None if no Finnish headings block is found (e.g., old-format pages
    that don't use the Next.js SSR structure).
    """
    fi_idx = text.find('\\"lang\\":\\"fi\\",\\"headings\\":[')
    if fi_idx < 0:
        return None
    # The data lives inside a <script>self.__next_f.push([1,"..."])</script> block.
    # Find the enclosing script tag by searching backwards, then forward for close.
    script_start = text.rfind('<script', 0, fi_idx)
    if script_start < 0:
        return None
    script_end = text.find('</script>', fi_idx)
    if script_end < 0:
        script_end = len(text)
    return text[script_start:script_end]


def _is_section_heading_id(heading_id: str) -> bool:
    """Return True if headingId identifies a section (§), not a chapter or other element."""
    # sec_N              → simple section (statute without chapters)
    # chp_X__sec_N       → section nested under chapter X
    # chp_X              → chapter heading → False
    # entryIntoForce_*   → amendment voimaantulo note → False
    # OT18, etc.         → miscellaneous → False
    return heading_id.startswith('sec_') or '__sec_' in heading_id


def _is_chapter_heading_id(heading_id: str) -> bool:
    """Return True if headingId identifies a chapter (luku)."""
    return heading_id.startswith('chp_') and '__' not in heading_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class HeadingResult(TypedDict):
    sections: list[str]
    chapters: list[str]


def extract_headings_from_html(html_bytes: bytes | str) -> HeadingResult:
    """Extract section and chapter headings from a Finlex statute HTML page.

    Args:
        html_bytes: Raw HTML content, either bytes (UTF-8) or str.

    Returns:
        A dict with keys:
            'sections': list of section labels in ToC order, e.g. ['1 §', '2 a §', ...]
            'chapters': list of chapter labels in ToC order, e.g. ['1 luku', '2 luku', ...]

    Both lists preserve the order they appear in the table of contents.
    Returns empty lists if the page does not contain the expected Next.js structure.
    """
    if isinstance(html_bytes, bytes):
        text = html_bytes.decode('utf-8', errors='replace')
    else:
        text = html_bytes

    fi_block = _find_fi_block(text)
    if fi_block is None:
        return HeadingResult(sections=[], chapters=[])

    sections: list[str] = []
    chapters: list[str] = []

    for _tagname, heading_text, heading_id in _ENTRY_PATTERN.findall(fi_block):
        if _is_section_heading_id(heading_id):
            sections.append(heading_text)
        elif _is_chapter_heading_id(heading_id):
            chapters.append(heading_text)
        # entryIntoForce_*, OT18, etc. → silently ignored

    return HeadingResult(sections=sections, chapters=chapters)


def extract_sections_from_html(html_bytes: bytes | str) -> list[str]:
    """Extract section headings from a Finlex statute HTML page.

    This is the primary public interface — returns only section labels (§),
    excluding chapters (luku), voimaantulo notes, and other structural elements.

    Args:
        html_bytes: Raw HTML content, either bytes (UTF-8) or str.

    Returns:
        List of section labels in document order, e.g. ['1 §', '2 §', '2 a §', '3 §', ...]
        Returns an empty list if the page does not contain the expected structure.

    Examples:
        >>> sections = extract_sections_from_html(open('2020_369.html', 'rb').read())
        >>> sections
        ['1 §', '2 §', '3 §', '4 §', '5 §', '5 a §', '6 §', '7 §', '8 §', '8 a §', '9 §', '10 §', '11 §']
    """
    return extract_headings_from_html(html_bytes)['sections']


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    """Run a quick self-test against the LawVM fetch archive.

    Usage (from LawVM/ dir):
        uv run python scripts/html_section_extractor.py
    """
    import sys
    from pathlib import Path

    # Add LawVM src to path
    sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

    try:
        from lawvm.fetch_archive import FetchArchive  # ty: ignore[unresolved-import]
    except ImportError:
        print('ERROR: could not import lawvm.fetch_archive — run from LawVM/ dir with uv run', file=sys.stderr)
        sys.exit(1)

    ARCHIVE_PATH = Path(__file__).parent.parent / '.tmp' / 'finlex_archive.db'
    if not ARCHIVE_PATH.exists():
        print(f'ERROR: archive not found at {ARCHIVE_PATH}', file=sys.stderr)
        sys.exit(1)

    # Known test cases: (sid, url, expected_sections, expected_chapters)
    TEST_CASES: list[tuple[str, str, int, int]] = [
        # Small statute, no chapters
        ('2020/369', 'https://www.finlex.fi/fi/lainsaadanto/2020/369', 13, 0),
        # Medium statute, no chapters
        ('2018/1121', 'https://www.finlex.fi/fi/lainsaadanto/2018/1121', 33, 0),
        # Medium statute, no chapters (regex badly undercounted: 38 vs 84)
        ('2017/444', 'https://www.finlex.fi/fi/lainsaadanto/2017/444', 84, 9),
        # Large statute, 12 chapters (regex badly overcounted: 186 vs ~179)
        ('2016/81', 'https://www.finlex.fi/fi/lainsaadanto/2016/81', 179, 14),
        # Medium statute, 18 chapters
        ('2017/531', 'https://www.finlex.fi/fi/lainsaadanto/2017/531', 148, 18),
        # Large statute, no chapters
        ('2015/410', 'https://www.finlex.fi/fi/lainsaadanto/2015/410', 157, 0),
        # Small statute with letter variants (1 a §, etc.)
        ('2018/1388', 'https://www.finlex.fi/fi/lainsaadanto/2018/1388', 17, 0),
    ]

    archive = FetchArchive(ARCHIVE_PATH)
    all_pass = True

    print(f'{"Statute":<12} {"Sections":>8} {"Chapters":>8}  {"Status"}')
    print('-' * 55)

    for sid, url, exp_sec, exp_chp in TEST_CASES:
        html = archive.get_latest(url)
        if html is None:
            print(f'{sid:<12} {"N/A":>8} {"N/A":>8}  SKIP (not in cache)')
            continue

        result = extract_headings_from_html(html)
        n_sec = len(result['sections'])
        n_chp = len(result['chapters'])

        ok = (n_sec == exp_sec) and (n_chp == exp_chp)
        status = 'PASS' if ok else f'FAIL (expected {exp_sec}s/{exp_chp}c)'
        if not ok:
            all_pass = False

        print(f'{sid:<12} {n_sec:>8} {n_chp:>8}  {status}')

    print()
    if all_pass:
        print('All tests passed.')
    else:
        print('Some tests FAILED.')
        sys.exit(1)
