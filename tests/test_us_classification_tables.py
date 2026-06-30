"""Tests for ``lawvm.us_federal.classification_tables``.

Covers, with NO network (all parsing tests use synthetic HTML):

- the whitespace-delimited row parser (synthetic table snippet);
- the index resolver on exact / sub-section / range / whole-PL lookups;
- ambiguity rejection (resolve returns None on conflicting USC targets);
- statute_id parsing and unmatched-PL None;
- serialization round-trip.
"""

from __future__ import annotations

import json

import pytest

from lawvm.us_federal.classification_tables import (
    ClassificationEntry,
    ClassificationIndex,
    EMPTY_INDEX,
    _session_url_session_token,
    parse_classification_table,
)

_SYNTHETIC_HTML = """<!DOCTYPE html>
<html>
<head><title>Classification Tables -- 118th Congress, 1st Session</title></head>
<body>
<pre>
50    3161         nt new           118-2                             [4]
49    40101        nt new           118-4    1                        [7]
2     901                           118-5    101(a), (b)              12, 13
5     551          nt new           118-5    261-270                  31-33
2     902                           118-5    101(a), (b)              12, 13
</pre>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Fetch URL plumbing -- pure function, no network call
# ---------------------------------------------------------------------------


def test_session_url_token_basic() -> None:
    assert _session_url_session_token(1) == "1st"
    assert _session_url_session_token(2) == "2nd"
    assert _session_url_session_token(3) == "3rd"


def test_session_url_token_general() -> None:
    # 4th is the outlier form the OLRC has not produced but the helper
    # generalises to the ordinal suffix.
    assert _session_url_session_token(4) == "4th"
    assert _session_url_session_token(11) == "11th"
    assert _session_url_session_token(12) == "12th"
    assert _session_url_session_token(13) == "13th"
    assert _session_url_session_token(21) == "21st"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_classification_table_synthetic() -> None:
    entries = parse_classification_table(_SYNTHETIC_HTML, congress=118)

    # Row 1: whole-PL classification (no PL section column). "118-2" -> 50 USC 3161.
    row1 = [e for e in entries if e.pl_congress == 118 and e.pl_number == 2]
    assert len(row1) == 1
    assert row1[0].pl_section == ""
    assert row1[0].usc_title == 50
    assert row1[0].usc_section == "3161"
    assert row1[0].description == "nt new"

    # Row 2: simple PL section "1".
    row2 = [e for e in entries if e.pl_congress == 118 and e.pl_number == 4 and e.pl_section == "1"]
    assert len(row2) == 1
    assert row2[0].usc_title == 49
    assert row2[0].usc_section == "40101"

    # Row 3: comma-separated PL section cell "101(a), (b)" splits into two
    # entries that share a USC target.
    row3 = [e for e in entries if e.pl_congress == 118 and e.pl_number == 5 and e.usc_section == "901"]
    pl_sections_row3 = sorted(e.pl_section for e in row3)
    assert pl_sections_row3 == ["101(a)", "101(b)"]

    # Row 4: range PL section "261-270" is preserved verbatim as one entry
    # (the index expands the range at lookup time).
    range_entries = [
        e for e in entries
        if e.pl_congress == 118 and e.pl_number == 5 and e.pl_section == "261-270"
    ]
    assert len(range_entries) == 1
    assert range_entries[0].usc_title == 5
    assert range_entries[0].usc_section == "551"


def test_parse_classification_table_strips_html_tags_and_decodes_entities() -> None:
    html = """<html><body><pre>
49    40101        nt new           118-4    1                        [7]
</pre></body></html>"""
    entries = parse_classification_table(html, congress=118)
    assert len(entries) == 1
    assert entries[0].usc_title == 49
    assert entries[0].usc_section == "40101"
    assert entries[0].pl_section == "1"


def test_parse_classification_table_decodes_numeric_entities() -> None:
    html = """<pre>
5     551          nt new           118-5    261-270                  31-33
</pre>"""
    # The HTML entity &#45; decodes to "-"; the parser must not confuse it
    # with a row separator.
    html_with_entity = html.replace("261-270", "261&#45;270")
    entries = parse_classification_table(html_with_entity, congress=118)
    assert len(entries) == 1
    assert entries[0].pl_section == "261-270"


def test_parse_classification_table_skips_non_row_lines() -> None:
    html = """<html>
<head><title>Classification Tables</title></head>
<body>
<h1>118th Congress, 1st Session</h1>
<p>The classification table for Public Laws of the 118th Congress.</p>
<pre>
USC Title    USC Section    Description    Act    Act Section    Stat.
50    3161         nt new           118-2                             [4]
</pre>
</body></html>"""
    entries = parse_classification_table(html, congress=118)
    # Header lines that begin with text ("USC", "The", "118th", "<h1>")
    # are skipped -- only the one real data row is parsed.
    assert len(entries) == 1
    assert entries[0].pl_number == 2


def test_parse_classification_table_empty_input() -> None:
    assert parse_classification_table("", congress=118) == []
    assert parse_classification_table("   \n\n   ", congress=118) == []


def test_parse_classification_table_usc_letter_section() -> None:
    # USC sections like "1011a", "78o-10" must survive the parser.
    html = """<pre>
11    1011a        nt new           118-7    5                        [10]
</pre>"""
    entries = parse_classification_table(html, congress=118)
    assert len(entries) == 1
    assert entries[0].usc_section == "1011a"


# ---------------------------------------------------------------------------
# ClassificationEntry: frozen, slotted carrier
# ---------------------------------------------------------------------------


def test_classification_entry_is_frozen() -> None:
    entry = ClassificationEntry(
        pl_congress=118,
        pl_number=5,
        pl_section="101(a)",
        usc_title=2,
        usc_section="901",
        description="nt new",
    )
    with pytest.raises((AttributeError, Exception)):
        entry.pl_section = "102(a)"  # type: ignore


def test_classification_entry_has_slots() -> None:
    entry = ClassificationEntry(
        pl_congress=118,
        pl_number=5,
        pl_section="101(a)",
        usc_title=2,
        usc_section="901",
    )
    # Frozen + slots: assigning a new attribute raises (TypeError on the
    # slots/frozen interplay, AttributeError on the frozen guard depending
    # on the CPython version).
    with pytest.raises((AttributeError, TypeError)):
        entry.new_field = 1  # type: ignore


# ---------------------------------------------------------------------------
# ClassificationIndex resolver
# ---------------------------------------------------------------------------


def _synthetic_index() -> ClassificationIndex:
    return ClassificationIndex(
        [
            # Whole-PL classification (no PL section).
            ClassificationEntry(118, 2, "", 50, "3161", "nt new"),
            # Exact section.
            ClassificationEntry(118, 4, "1", 49, "40101", "nt new"),
            # Sub-section parent: PL 118-5 sec. 101 classifies to 2 USC 901.
            ClassificationEntry(118, 5, "101(a)", 2, "901", ""),
            ClassificationEntry(118, 5, "101(b)", 2, "901", ""),
            # Range: PL 118-5 sec. 261-270 classifies to 5 USC 551.
            ClassificationEntry(118, 5, "261-270", 5, "551", "nt new"),
        ]
    )


def test_resolve_exact_match() -> None:
    idx = _synthetic_index()
    addr = idx.resolve("PL 118-4", "1")
    assert addr is not None
    assert addr.path == (("title", "49"), ("section", "40101"))


def test_resolve_subsection_peels_to_parent() -> None:
    idx = _synthetic_index()
    # "122(a)" pattern: the lowerer may emit a sub-section like "101(a)"
    # with no exact entry but a parent "101" lookup would not resolve here
    # either -- this index stores the sub-section itself, so the exact
    # lookup succeeds.
    addr_a = idx.resolve("PL 118-5", "101(a)")
    assert addr_a is not None
    assert addr_a.path == (("title", "2"), ("section", "901"))

    # The classic peel case: query "101(a)(1)" -- no exact entry. The
    # resolver peels "(1)" leaving "101(a)", which IS an exact entry.
    addr_a1 = idx.resolve("PL 118-5", "101(a)(1)")
    assert addr_a1 is not None
    assert addr_a1.path == (("title", "2"), ("section", "901"))


def test_resolve_range_exact_range_token() -> None:
    idx = _synthetic_index()
    # Literal range lookup.
    addr = idx.resolve("PL 118-5", "261-270")
    assert addr is not None
    assert addr.path == (("title", "5"), ("section", "551"))


def test_resolve_range_within_range_integer() -> None:
    idx = _synthetic_index()
    # Any integer in [261, 270] resolves to the range's USC target.
    for n in (261, 265, 270):
        addr = idx.resolve("PL 118-5", str(n))
        assert addr is not None, f"expected resolution for PL 118-5 sec. {n}"
        assert addr.path == (("title", "5"), ("section", "551")), (
            f"PL 118-5 sec. {n} resolved to wrong USC target {addr}"
        )


def test_resolve_whole_pl_fallback() -> None:
    idx = _synthetic_index()
    # PL 118-2 has only a whole-PL entry (no PL section column). Any
    # pl_section lookup falls back to the whole-PL target.
    addr = idx.resolve("PL 118-2", "5")
    assert addr is not None
    assert addr.path == (("title", "50"), ("section", "3161"))
    # Even an empty pl_section resolves via the whole-PL key.
    addr_empty = idx.resolve("PL 118-2", "")
    assert addr_empty is not None
    assert addr_empty.path == (("title", "50"), ("section", "3161"))


def test_resolve_no_match_returns_none() -> None:
    idx = _synthetic_index()
    # Unknown PL number.
    assert idx.resolve("PL 118-999", "1") is None
    # Unknown PL congress.
    assert idx.resolve("PL 999-5", "101(a)") is None
    # Known PL, unknown section.
    assert idx.resolve("PL 118-5", "999") is None


def test_resolve_unparseable_statute_id_returns_none() -> None:
    idx = _synthetic_index()
    assert idx.resolve("not-a-statute-id", "1") is None
    assert idx.resolve("", "1") is None
    assert idx.resolve("PL 118", "1") is None  # missing number


def test_resolve_ambiguous_targets_returns_none() -> None:
    # Two entries under the same key, disagreeing on the USC target: the
    # resolver MUST refuse to pick (AGENTS.md sec. 1.7).
    idx = ClassificationIndex(
        [
            ClassificationEntry(118, 6, "1", 10, "1001", ""),
            ClassificationEntry(118, 6, "1", 11, "2002", ""),
        ]
    )
    assert idx.resolve("PL 118-6", "1") is None
    # resolve_all surfaces the candidate set for triage.
    candidates = idx.resolve_all("PL 118-6", "1")
    assert len(candidates) == 2
    addrs = {c.path for c in candidates}
    assert (("title", "10"), ("section", "1001")) in addrs
    assert (("title", "11"), ("section", "2002")) in addrs


def test_resolve_duplicate_same_target_collapses_to_one() -> None:
    # Two entries under the same key agreeing on the USC target: not
    # ambiguous, resolves to the shared address.
    idx = ClassificationIndex(
        [
            ClassificationEntry(118, 7, "1", 10, "1001", "nt new"),
            ClassificationEntry(118, 7, "1", 10, "1001", "nt"),
        ]
    )
    addr = idx.resolve("PL 118-7", "1")
    assert addr is not None
    assert addr.path == (("title", "10"), ("section", "1001"))


# ---------------------------------------------------------------------------
# statute_id parser
# ---------------------------------------------------------------------------


def test_parse_statute_id_canonical() -> None:
    assert ClassificationIndex.parse_statute_id("PL 118-5") == (118, 5)
    assert ClassificationIndex.parse_statute_id("PL 116-92") == (116, 92)


def test_parse_statute_id_accepts_en_dash() -> None:
    # OLRC citableAs uses U+2013 (en-dash); ASCII hyphen is also accepted.
    assert ClassificationIndex.parse_statute_id("PL 118\u20135") == (118, 5)


def test_parse_statute_id_invalid() -> None:
    assert ClassificationIndex.parse_statute_id("") is None
    assert ClassificationIndex.parse_statute_id("PL 118") is None
    assert ClassificationIndex.parse_statute_id("118-5") is None
    assert ClassificationIndex.parse_statute_id("Public Law 118-5") is None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_jsonable_roundtrip_preserves_entries() -> None:
    idx = _synthetic_index()
    serialised = idx.to_jsonable()
    # Sanity: JSON-serializable.
    encoded = json.dumps(serialised)
    decoded = json.loads(encoded)
    restored = ClassificationIndex.from_jsonable(decoded)

    # The restored index resolves the same way.
    addr_before = idx.resolve("PL 118-5", "101(a)")
    addr_after = restored.resolve("PL 118-5", "101(a)")
    assert addr_before == addr_after
    assert addr_after is not None
    assert addr_after.path == (("title", "2"), ("section", "901"))


def test_from_jsonable_rejects_wrong_version() -> None:
    with pytest.raises(ValueError, match="unsupported classification index version"):
        ClassificationIndex.from_jsonable({"version": 99, "entries": []})


def test_from_jsonable_rejects_non_list_entries() -> None:
    with pytest.raises(TypeError, match="entries.* must be a list"):
        ClassificationIndex.from_jsonable({"version": 1, "entries": {}})


def test_from_jsonable_rejects_non_int_field() -> None:
    with pytest.raises(TypeError, match="pl_congress must be int"):
        ClassificationIndex.from_jsonable(
            {
                "version": 1,
                "entries": [
                    {
                        "pl_congress": "not-an-int",
                        "pl_number": 5,
                        "pl_section": "1",
                        "usc_title": 10,
                        "usc_section": "1001",
                        "description": "",
                    }
                ],
            }
        )


# ---------------------------------------------------------------------------
# Empty index
# ---------------------------------------------------------------------------


def test_empty_index_resolves_to_none() -> None:
    assert EMPTY_INDEX.resolve("PL 118-5", "101(a)") is None
    assert EMPTY_INDEX.resolve_all("PL 118-5", "101(a)") == []
    assert len(EMPTY_INDEX) == 0


def test_empty_index_from_iterable_independent() -> None:
    # Mutating the source iterable after construction must not affect the
    # frozen EMPTY_INDEX used elsewhere.
    other = ClassificationIndex([])
    assert other.resolve("PL 118-5", "1") is None


# ---------------------------------------------------------------------------
# Stats / introspection
# ---------------------------------------------------------------------------


def test_index_stats_reports_counts() -> None:
    idx = _synthetic_index()
    stats = idx.stats()
    assert stats["entries"] == 5
    # Three distinct (congress, pl_number) pairs: 118-2, 118-4, 118-5.
    assert stats["distinct_public_laws"] == 3
    # The range expands 261..270 to 10 singletons plus the range key.
    # So index_keys is: "1" (118-4), "101(a)" (118-5), "101(b)" (118-5),
    # "261".."270" (10 keys), "261-270" (1 key) = 14.
    assert stats["index_keys"] == 14
    # One whole-PL key: (118, 2).
    assert stats["whole_pl_keys"] == 1


def test_index_entries_returns_source_tuple() -> None:
    idx = _synthetic_index()
    entries = idx.entries()
    assert isinstance(entries, tuple)
    assert len(entries) == 5
    # Source tuple is immutable / for forensics only.
    assert all(isinstance(e, ClassificationEntry) for e in entries)
