"""test_finlex_html_rsc.py — Unit tests for Finlex RSC JSON parsing.

Tests the new JSON-based parsing path in finlex_html.py, including:
- _escape_ctrl_in_strings: re-escaping literal control chars inside strings
- _find_rsc_documenttocs_block: finding the right RSC push block
- _unescape_rsc_json_string: standard backslash unescaping
- _parse_rsc_json_headings: full primary path
- _parse_html_headings / _parse_html_heading_entries: dispatcher with fallback
- Ghost entry elimination (the root cause this rewrite was designed to fix)
"""

from __future__ import annotations

import json

from lawvm.finland.finlex_html import (
    _escape_ctrl_in_strings,
    _find_fi_block_legacy,
    _find_rsc_documenttocs_block,
    _is_chapter_heading_id,
    _is_section_heading_id,
    _parse_html_heading_entries,
    _parse_html_headings,
    _parse_rsc_json_headings,
    _unescape_rsc_json_string,
)


# ---------------------------------------------------------------------------
# Helpers for constructing synthetic RSC HTML pages
# ---------------------------------------------------------------------------

def _make_rsc_page(headings_json: str, include_swe_block: bool = False) -> bytes:
    """Build a minimal synthetic Finlex RSC HTML page.

    Constructs a self.__next_f.push([1,"..."]) block containing a
    documentToCs structure.  The headings_json argument is the raw JSON
    for the headings array (already valid JSON, not escaped).

    If include_swe_block is True, a second RSC block is added later in the
    page that contains a Swedish ToC inlined as an array (simulating the
    ghost-entry source that the old regex approach incorrectly captured).
    """
    # Build the documentToCs JSON value (unescaped form)
    doc_value = (
        '2b:["$","$32",null,{"children":["$","$L79",null,'
        '{"locale":"fi","documentViews":{"fin":"$L7a","swe":"$L7b"},'
        '"documentToCs":{"fin":["$","$L7c","988026",{"headings":'
        + headings_json
        + '}],"swe":"$L7d"'
        '}}]}]'
    )

    # Escape the value for embedding inside push([1,"..."])
    escaped = doc_value.replace('\\', '\\\\"').replace('"', '\\"')
    # Actually the escaping in RSC is: literal " -> \" and literal \ -> \\
    # but Python strings make this tricky. Let's just use json.dumps to escape:
    escaped_inner = json.dumps(doc_value)[1:-1]  # strip outer quotes

    page = (
        '<!DOCTYPE html><html><head></head><body>'
        '<script>self.__next_f.push([1,"'
        + escaped_inner
        + '\\n"])</script>'
    )

    if include_swe_block:
        # Simulate the Swedish ToC block that old code would accidentally capture
        swe_heading = (
            '{"element":{"tagName":"num","attributes":{"isNumHeading":"true"},'
            '"children":[{"text":"8 a §"}],"headingId":"sec_8a"},'
            '"nextElement":{"tagName":"section","attributes":{},"children":[]},'
            '"subHeadings":[]}'
        )
        swe_doc_value = (
            '70:["$","$swe",null,{"fin":["$","$L9a",null,{"headings":['
            + swe_heading
            + ']}],"swe":["$","$L9b","999999",{"headings":['
            + swe_heading
            + ']}]}]'
        )
        swe_escaped = json.dumps(swe_doc_value)[1:-1]
        page += (
            '<script>self.__next_f.push([1,"'
            + swe_escaped
            + '\\n"])</script>'
        )

    page += '</body></html>'
    return page.encode('utf-8')


def _make_simple_section_heading(num: str, heading_id: str, e_id: str | None = None) -> dict:
    """Return a heading dict for a simple section."""
    attrs: dict = {}
    if e_id:
        attrs['eId'] = e_id
    return {
        'element': {
            'tagName': 'num',
            'attributes': {'isNumHeading': 'true'},
            'children': [{'text': num}],
            'headingId': heading_id,
        },
        'nextElement': {
            'tagName': 'section',
            'attributes': attrs,
            'children': [],
        },
        'subHeadings': [],
    }


def _make_chapter_with_sections(
    chapter_num: str,
    chapter_id: str,
    sections: list[tuple[str, str]],
) -> dict:
    """Return a heading dict for a chapter containing section subHeadings."""
    sub = [_make_simple_section_heading(num, sid) for num, sid in sections]
    return {
        'element': {
            'tagName': 'num',
            'attributes': {'isNumHeading': 'true'},
            'children': [{'text': chapter_num}],
            'headingId': chapter_id,
        },
        'nextElement': {
            'tagName': 'section',
            'attributes': {},
            'children': [],
        },
        'subHeadings': sub,
    }


# ---------------------------------------------------------------------------
# Tests: _escape_ctrl_in_strings
# ---------------------------------------------------------------------------

def test_escape_ctrl_leaves_structural_whitespace_alone() -> None:
    """Newlines outside string values (JSON structure) must not be escaped."""
    s = '{"a": "hello"}\n{"b": "world"}'
    result = _escape_ctrl_in_strings(s)
    # The newline between the two objects is structural, not inside a string
    assert '\n' in result


def test_escape_ctrl_escapes_newline_inside_string() -> None:
    """Literal newline inside a JSON string value must be escaped to \\n."""
    s = '{"text": "line1\nline2"}'
    result = _escape_ctrl_in_strings(s)
    assert '\\n' in result
    assert 'line1\nline2' not in result
    # Result should be parseable
    parsed = json.loads(result)
    assert parsed['text'] == 'line1\nline2'


def test_escape_ctrl_escapes_tab_inside_string() -> None:
    """Literal tab inside a JSON string value must be escaped to \\t."""
    s = '{"text": "col1\tcol2"}'
    result = _escape_ctrl_in_strings(s)
    parsed = json.loads(result)
    assert parsed['text'] == 'col1\tcol2'


def test_escape_ctrl_handles_backslash_escapes_in_string() -> None:
    """Existing \\n escapes inside strings must not be double-escaped."""
    s = '{"text": "already\\nescaped"}'
    result = _escape_ctrl_in_strings(s)
    # Should still be valid JSON and value unchanged
    parsed = json.loads(result)
    assert parsed['text'] == 'already\nescaped'


# ---------------------------------------------------------------------------
# Tests: _unescape_rsc_json_string
# ---------------------------------------------------------------------------

def test_unescape_rsc_unescapes_double_quotes() -> None:
    # RSC raw_inner has \" (backslash + quote) for each JSON quote.
    # In Python this is written as '\\"' (the string \" is 2 chars).
    raw = '{\\"key\\":\\"value\\"}'
    result = _unescape_rsc_json_string(raw)
    assert result == '{"key":"value"}'


def test_unescape_rsc_unescapes_backslashes() -> None:
    # RSC encodes \\ as \\\\ (two backslashes in raw_inner).
    # To write two backslashes in a Python string: '\\\\'.
    raw = '{\\"path\\":\\"C:\\\\\\\\Windows\\"}'
    result = _unescape_rsc_json_string(raw)
    assert result == '{"path":"C:\\\\Windows"}'


def test_unescape_rsc_unescapes_newline_sequences() -> None:
    # RSC encodes \n as \\n (backslash + n in raw_inner).
    raw = '{\\"text\\":\\"line1\\nline2\\"}'
    result = _unescape_rsc_json_string(raw)
    assert result == '{"text":"line1\nline2"}'


# ---------------------------------------------------------------------------
# Tests: _is_section_heading_id / _is_chapter_heading_id
# ---------------------------------------------------------------------------

def test_is_section_bare() -> None:
    assert _is_section_heading_id('sec_5')
    assert _is_section_heading_id('sec_12a')


def test_is_section_scoped() -> None:
    assert _is_section_heading_id('chp_3__sec_7')
    assert _is_section_heading_id('chp_1__sec_2a')


def test_is_section_excludes_chapter() -> None:
    assert not _is_section_heading_id('chp_3')
    assert not _is_section_heading_id('OT5')


def test_is_chapter_bare() -> None:
    assert _is_chapter_heading_id('chp_1')
    assert _is_chapter_heading_id('chp_12')


def test_is_chapter_part_scoped() -> None:
    assert _is_chapter_heading_id('part_1__chp_1')
    assert _is_chapter_heading_id('part_3__chp_15')


def test_is_chapter_excludes_scoped() -> None:
    assert not _is_chapter_heading_id('chp_3__sec_7')


def test_is_chapter_excludes_section() -> None:
    assert not _is_chapter_heading_id('sec_5')


# ---------------------------------------------------------------------------
# Tests: _find_rsc_documenttocs_block
# ---------------------------------------------------------------------------

def test_find_rsc_block_returns_none_for_empty_page() -> None:
    assert _find_rsc_documenttocs_block('<html></html>') is None


def test_find_rsc_block_returns_none_when_no_documenttocs() -> None:
    # Page has an RSC push block but it does not contain the documentToCs key.
    page = '<script>self.__next_f.push([1,"some content without the magic key\\n"])</script>'
    assert _find_rsc_documenttocs_block(page) is None


def test_find_rsc_block_finds_documenttocs_block() -> None:
    inner = '2b:[\\"$\\",\\"$32\\",null,{\\"documentToCs\\":{\\"fin\\":\\"$L7c\\"}}]'
    page = f'<script>self.__next_f.push([1,"{inner}\\n"])</script>'
    result = _find_rsc_documenttocs_block(page)
    assert result is not None
    assert 'documentToCs' in result


def test_find_rsc_block_strips_closing_bracket() -> None:
    """The returned string must NOT end with \"])  (the closing push syntax)."""
    inner = '2b:[\\"$\\",null,{\\"documentToCs\\":{\\"fin\\":\\"$L7c\\"}}]'
    page = f'<script>self.__next_f.push([1,"{inner}\\n"])</script>'
    result = _find_rsc_documenttocs_block(page)
    assert result is not None
    assert not result.strip().endswith('"])')


# ---------------------------------------------------------------------------
# Tests: _parse_rsc_json_headings (primary path)
# ---------------------------------------------------------------------------

def test_parse_rsc_json_simple_sections() -> None:
    """Flat statute (no chapters) with a few sections."""
    headings = [
        _make_simple_section_heading('1 §', 'sec_1'),
        _make_simple_section_heading('2 §', 'sec_2'),
        _make_simple_section_heading('3 §', 'sec_3'),
    ]
    page = _make_rsc_page(json.dumps(headings))
    text = page.decode('utf-8')
    result = _parse_rsc_json_headings(text)
    assert result is not None
    assert result['sections'] == ['1 §', '2 §', '3 §']
    assert result['chapters'] == []


def test_parse_rsc_json_chapters_with_sections() -> None:
    """Statute with chapters, sections as subHeadings."""
    headings = [
        _make_chapter_with_sections('1 luku', 'chp_1', [
            ('1 §', 'chp_1__sec_1'),
            ('2 §', 'chp_1__sec_2'),
        ]),
        _make_chapter_with_sections('2 luku', 'chp_2', [
            ('3 §', 'chp_2__sec_3'),
        ]),
    ]
    page = _make_rsc_page(json.dumps(headings))
    text = page.decode('utf-8')
    result = _parse_rsc_json_headings(text)
    assert result is not None
    assert result['chapters'] == ['1 luku', '2 luku']
    assert result['sections'] == ['1 §', '2 §', '3 §']


def test_parse_rsc_json_returns_none_for_unparseable_page() -> None:
    """Page with no documentToCs block returns None from JSON path."""
    page = b'<html><body><script>self.__next_f.push([0,"garbage"])</script></body></html>'
    result = _parse_rsc_json_headings(page.decode('utf-8'))
    assert result is None


def test_parse_rsc_json_no_ghost_entries_from_swedish_block() -> None:
    """Swedish ghost entries in a separate RSC block must not contaminate Finnish list.

    This is the root cause the rewrite was designed to fix.  The Finnish fin
    array in documentToCs only has sections 1-3.  A later RSC block has a
    Swedish ToC with '8 a §' inlined as an array — the old regex path would
    capture this ghost entry; the JSON path must not.
    """
    fi_headings = [
        _make_simple_section_heading('1 §', 'sec_1'),
        _make_simple_section_heading('2 §', 'sec_2'),
        _make_simple_section_heading('3 §', 'sec_3'),
    ]
    page = _make_rsc_page(json.dumps(fi_headings), include_swe_block=True)
    text = page.decode('utf-8')

    # JSON path: no ghost entries
    result = _parse_rsc_json_headings(text)
    assert result is not None
    assert result['sections'] == ['1 §', '2 §', '3 §']
    assert '8 a §' not in result['sections']


# ---------------------------------------------------------------------------
# Tests: _parse_html_headings (dispatcher — primary + fallback)
# ---------------------------------------------------------------------------

def test_parse_html_headings_uses_json_path() -> None:
    """Dispatcher returns JSON-path result for a well-formed RSC page."""
    headings = [
        _make_simple_section_heading('1 §', 'sec_1'),
        _make_simple_section_heading('2 §', 'sec_2'),
    ]
    page = _make_rsc_page(json.dumps(headings))
    result = _parse_html_headings(page)
    assert result['sections'] == ['1 §', '2 §']


def test_parse_html_headings_returns_empty_for_bot_block_page() -> None:
    """A minimal HTML page with no RSC structure returns empty lists."""
    page = b'<html><body>Access denied</body></html>'
    result = _parse_html_headings(page)
    assert result['sections'] == []
    assert result['chapters'] == []


def test_parse_html_headings_falls_back_gracefully_to_empty() -> None:
    """When neither JSON path nor legacy path finds headings, returns empty HeadingResult.

    This tests that the dispatcher never raises — it degrades to empty lists
    for pages with unexpected structure (bot-blocked responses, format changes).
    """
    # A plausible-looking HTML page with no RSC push blocks at all
    page = (
        b'<html><head><title>Finlex</title></head>'
        b'<body><p>Virhe: sivu ei latautunut.</p></body>'
        b'</html>'
    )
    result = _parse_html_headings(page)
    assert result['sections'] == []
    assert result['chapters'] == []


def test_parse_html_headings_legacy_fin_marker_path() -> None:
    """Legacy FIN_MARKER path: page has \\\"fin\\\":[\\\"$\\\" but no documentToCs JSON.

    The legacy strategy 1 in _find_fi_block_legacy looks for the FIN_MARKER
    prefix and extracts up to the SWE_MARKER.  This tests that the _ENTRY_PATTERN
    regex finds headings in such a block.
    """
    # Build a minimal FIN_MARKER style block that _ENTRY_PATTERN can parse.
    # The RSC text here uses single-backslash-escaped quotes (\") as in current pages.
    # FIN_MARKER = '\\"fin\\":[\\"$\\"'  (1 backslash before each quote)
    # _ENTRY_PATTERN also uses single-backslash escaping.
    fi_entry = (
        '\\"tagName\\":\\"num\\",'
        '\\"attributes\\":{\\"isNumHeading\\":\\"true\\"},'
        '\\"children\\":[{\\"text\\":\\"7 \xa7\\"}],'
        '\\"headingId\\":\\"sec_7\\"'
    )
    # Construct a block that starts with FIN_MARKER and contains the entry
    block_text = '\\"fin\\":[\\"$\\",\\"$L7c\\",null,{' + fi_entry + '}]'
    # There is no SWE_MARKER (\\"swe\\":[\\"$\\") in this block, so legacy
    # strategy 1 will return to end of script block.
    page = (
        b'<html><body>'
        b'<script>' + block_text.encode('utf-8') + b'</script>'
        b'</body></html>'
    )
    result = _parse_html_headings(page)
    # The legacy path should find "7 §"
    assert '7 §' in result['sections']


# ---------------------------------------------------------------------------
# Tests: _parse_html_heading_entries (rich entries via JSON path)
# ---------------------------------------------------------------------------

def test_parse_html_heading_entries_returns_heading_entries() -> None:
    """JSON path returns HeadingEntry objects with required fields."""
    headings = [
        _make_simple_section_heading('1 §', 'sec_1', e_id='sec_1v20200001'),
        _make_simple_section_heading('2 §', 'sec_2'),
    ]
    page = _make_rsc_page(json.dumps(headings))
    entries = _parse_html_heading_entries(page)
    assert len(entries) == 2
    assert entries[0]['heading_id'] == 'sec_1'
    assert entries[0]['text'] == '1 §'
    assert entries[0]['kind'] == 'section'
    assert entries[0]['eId'] == 'sec_1v20200001'
    assert entries[1]['eId'] is None


def test_parse_html_heading_entries_detects_repealed_sections() -> None:
    """Sections with 'on kumottu' in body text are marked is_repealed=True."""
    kumottu_heading = {
        'element': {
            'tagName': 'num',
            'attributes': {'isNumHeading': 'true'},
            'children': [{'text': '3 §'}],
            'headingId': 'sec_3',
        },
        'nextElement': {
            'tagName': 'section',
            'attributes': {},
            'children': [{'tagName': 'p', 'attributes': {}, 'children': [
                {'text': '3 § on kumottu L:lla 1234/2020.'},
            ]}],
        },
        'subHeadings': [],
    }
    normal_heading = _make_simple_section_heading('4 §', 'sec_4')
    page = _make_rsc_page(json.dumps([kumottu_heading, normal_heading]))
    entries = _parse_html_heading_entries(page)
    assert len(entries) == 2
    assert entries[0]['is_repealed'] is True
    assert entries[1]['is_repealed'] is False


def test_parse_html_heading_entries_chapter_with_heading_text() -> None:
    """Chapters with tagName==heading nextElement get heading_text populated."""
    chapter_with_otsikko = {
        'element': {
            'tagName': 'num',
            'attributes': {'isNumHeading': 'true'},
            'children': [{'text': '1 luku'}],
            'headingId': 'chp_1',
        },
        'nextElement': {
            'tagName': 'heading',
            'attributes': {'eId': 'chp_1__heading'},
            'children': [{'text': 'Yleiset säännökset'}],
        },
        'subHeadings': [],
    }
    page = _make_rsc_page(json.dumps([chapter_with_otsikko]))
    entries = _parse_html_heading_entries(page)
    assert len(entries) == 1
    assert entries[0]['kind'] == 'chapter'
    assert entries[0]['heading_text'] == 'Yleiset säännökset'
    assert entries[0]['eId'] == 'chp_1__heading'


def test_parse_html_heading_entries_returns_empty_for_empty_page() -> None:
    """Empty/bot-block page returns an empty list."""
    page = b'<html><body>Bot block</body></html>'
    entries = _parse_html_heading_entries(page)
    assert entries == []


# ---------------------------------------------------------------------------
# Tests: _find_fi_block_legacy (named fallback — verify it still works)
# ---------------------------------------------------------------------------

def test_find_fi_block_legacy_finds_fin_marker() -> None:
    """Legacy function finds the FIN_MARKER in a minimal escaped block."""
    text = (
        'some prefix '
        '\\"fin\\":[\\"$\\",[{\\"tagName\\":\\"num\\"}]]'
        '\\"swe\\":[\\"$\\",[]]'
        ' some suffix'
    )
    result = _find_fi_block_legacy(text)
    assert result is not None
    assert '\\"fin\\"' in result
    assert '\\"swe\\"' not in result  # scoped to before swe marker


def test_find_fi_block_legacy_returns_none_for_plain_page() -> None:
    """Legacy function returns None for a page with no RSC markers."""
    assert _find_fi_block_legacy('<html><body>No RSC here</body></html>') is None
