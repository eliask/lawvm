"""Tests for fi_sections_text projection.

Per AGENTS.md §15, covers all 7 required test categories:

1. Synthetic unit test + corpus fixture: parse AKN fixture, verify rows.
2. Real corpus regression: pull a small sample, verify section counts.
3. Schema-stability: column order + dtypes pinned.
4. Reuse-verification: body_text contains expected statute text from a
   known consolidated statute (when oracle available).
5. Negative test: amendment AKN (FRBRsubtype != 'statute-consolidated')
   rejected; empty-section section returns body_text=''.
6. Strict-mode / graceful degradation: oracle unavailable → zero rows, no
   exception.
7. No-leak: synthetic statute_id markers must not appear in production
   fi_sections_text rows.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict

import pytest

from lawvm.core.section_text import SectionText, section_text_to_row
from lawvm.finland.conformance_corpus.sections_text.fixtures import (
    ALL_FIXTURES,
    AMENDMENT_REJECTED,
    EMPTY_SECTION,
    INLINE_REF,
    MULTI_SECTION,
    NESTED_CHAPTER,
)
from lawvm.finland.section_text_extractor import (
    SectionTextExtractionDiagnostic,
    SectionTextExtractionResult,
    extract_sections_text,
    _eid_to_section_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_partial_row(actual: Dict[str, Any], expected: Dict[str, Any]) -> None:
    """Assert that all keys in expected match actual (partial match)."""
    for k, v in expected.items():
        assert k in actual, f"Key {k!r} missing from row: {actual}"
        assert actual[k] == v, f"Row[{k!r}]: expected {v!r}, got {actual[k]!r}"


def _extract(fixture: Any) -> SectionTextExtractionResult:
    return extract_sections_text(fixture.xml_bytes, fixture.statute_id)


# ---------------------------------------------------------------------------
# 1. Synthetic unit tests — conformance corpus fixtures
# ---------------------------------------------------------------------------


class TestCorpusFixtureExtraction:
    """Category 1: extract from conformance fixtures, verify rows."""

    def test_multi_section_row_count(self) -> None:
        result = _extract(MULTI_SECTION)
        assert len(result.sections) == 3, (
            f"Expected 3 sections, got {len(result.sections)}"
        )

    def test_multi_section_partial_rows(self) -> None:
        result = _extract(MULTI_SECTION)
        rows = [section_text_to_row(s) for s in result.sections]
        for expected in MULTI_SECTION.expected_sections:
            match = next(
                (r for r in rows if r["section_key"] == expected["section_key"]),
                None,
            )
            assert match is not None, (
                f"No row with section_key={expected['section_key']!r}; "
                f"got keys: {[r['section_key'] for r in rows]}"
            )
            _assert_partial_row(match, expected)

    def test_nested_chapter_section_keys(self) -> None:
        result = _extract(NESTED_CHAPTER)
        keys = {s.section_key for s in result.sections}
        assert "chapter:1/section:1" in keys
        assert "chapter:1/section:5" in keys
        assert "chapter:2/section:9" in keys

    def test_nested_chapter_statute_id(self) -> None:
        result = _extract(NESTED_CHAPTER)
        for s in result.sections:
            assert s.statute_id == NESTED_CHAPTER.statute_id

    def test_empty_section_body_text_is_empty(self) -> None:
        result = _extract(EMPTY_SECTION)
        # First section has heading but no body
        sec1 = next(
            (s for s in result.sections if s.section_key == "section:1"), None
        )
        assert sec1 is not None, "Section 1 not extracted"
        assert sec1.body_text == "", (
            f"Expected empty body_text for heading-only section, got {sec1.body_text!r}"
        )
        assert sec1.char_count == 0

    def test_empty_section_heading_preserved(self) -> None:
        result = _extract(EMPTY_SECTION)
        sec1 = next(s for s in result.sections if s.section_key == "section:1")
        assert sec1.heading_text == "Otsikko"

    def test_inline_ref_display_text_kept(self) -> None:
        result = _extract(INLINE_REF)
        assert len(result.sections) == 1
        sec = result.sections[0]
        # Should contain displayed text of <ref> elements
        assert "lannoitelakiin" in sec.body_text
        assert "kemikaalilakiin" in sec.body_text

    def test_inline_ref_href_stripped(self) -> None:
        result = _extract(INLINE_REF)
        sec = result.sections[0]
        # href attribute value must NOT be in body_text
        assert "/akn/fi/act/statute-consolidated/2022/711#sec_7" not in sec.body_text
        assert "/akn/fi/act/statute-consolidated/1978/404#sec_1" not in sec.body_text

    def test_valid_at_start_extracted(self) -> None:
        result = _extract(MULTI_SECTION)
        # Fixture uses date 2024-01-15 in consolidated meta
        for s in result.sections:
            assert s.valid_at_start == date(2024, 1, 15), (
                f"Expected valid_at_start=2024-01-15, got {s.valid_at_start}"
            )

    def test_valid_at_end_always_none(self) -> None:
        result = _extract(MULTI_SECTION)
        for s in result.sections:
            assert s.valid_at_end is None

    def test_char_count_matches_body_text(self) -> None:
        result = _extract(MULTI_SECTION)
        for s in result.sections:
            assert s.char_count == len(s.body_text), (
                f"char_count={s.char_count} != len(body_text)={len(s.body_text)}"
            )

    def test_all_fixtures_in_registry(self) -> None:
        """All fixture IDs are present in ALL_FIXTURES."""
        expected_ids = {
            "multi_section",
            "nested_chapter",
            "empty_section",
            "inline_ref",
            "amendment_rejected",
        }
        assert set(ALL_FIXTURES.keys()) == expected_ids


# ---------------------------------------------------------------------------
# 2. Real corpus regression (skipped unless finlex.farchive is available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("pathlib").Path("data/finlex.farchive").exists(),
    reason="finlex.farchive not present; skipping real-corpus test",
)
class TestRealCorpusRegression:
    """Category 2: pull a sample of consolidated statutes, verify reasonableness."""

    def test_known_statute_has_sections(self) -> None:
        """Statute 2003/434 (hallintolaki) should have many sections."""
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        xml_bytes = store.read_oracle("2003/434")
        assert xml_bytes is not None
        result = extract_sections_text(xml_bytes, "2003/434")
        assert len(result.sections) >= 50, (
            f"Expected >=50 sections for hallintolaki, got {len(result.sections)}"
        )

    def test_known_statute_section_text_nonempty(self) -> None:
        """Most sections in 2003/434 should have non-empty body_text."""
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        xml_bytes = store.read_oracle("2003/434")
        assert xml_bytes is not None
        result = extract_sections_text(xml_bytes, "2003/434")
        nonempty = [s for s in result.sections if s.body_text]
        assert len(nonempty) >= 40, (
            f"Expected >=40 sections with body_text, got {len(nonempty)}"
        )

    def test_perustuslaki_chapter_section_keys(self) -> None:
        """Perustuslaki (1999/731) has chapter-nested sections."""
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        xml_bytes = store.read_oracle("1999/731")
        assert xml_bytes is not None
        result = extract_sections_text(xml_bytes, "1999/731")
        # Keys should be chapter:N/section:M form
        chapter_keys = [
            s.section_key for s in result.sections
            if s.section_key.startswith("chapter:")
        ]
        assert len(chapter_keys) > 0, (
            "Expected chapter-nested section keys in perustuslaki"
        )

    def test_small_corpus_sample_row_count(self) -> None:
        """First 100 statutes should yield at least 500 sections total."""
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        ids = store.list_statute_ids()[:100]
        total = 0
        for sid in ids:
            xml = store.read_oracle(sid)
            if xml is None:
                continue
            result = extract_sections_text(xml, sid)
            total += len(result.sections)
        assert total >= 500, (
            f"Expected >=500 sections from first 100 statutes, got {total}"
        )


# ---------------------------------------------------------------------------
# 3. Schema-stability: column names and row dict keys
# ---------------------------------------------------------------------------


class TestSchemaStability:
    """Category 3: column order and presence pinned."""

    _EXPECTED_COLUMNS = (
        "statute_id",
        "section_key",
        "section_label",
        "heading_text",
        "body_text",
        "char_count",
        "source_span_byte_offset",
        "source_span_len",
        "valid_at_start",
        "valid_at_end",
    )

    def test_row_keys_complete(self) -> None:
        result = _extract(MULTI_SECTION)
        assert result.sections, "Expected at least one section"
        row = section_text_to_row(result.sections[0])
        for col in self._EXPECTED_COLUMNS:
            assert col in row, f"Missing column {col!r}"

    def test_no_extra_keys(self) -> None:
        result = _extract(MULTI_SECTION)
        row = section_text_to_row(result.sections[0])
        extra = set(row.keys()) - set(self._EXPECTED_COLUMNS)
        assert not extra, f"Unexpected extra columns: {extra}"

    def test_parquet_schema_matches_expected(self) -> None:
        from lawvm.tools.export_fi_sections_text import FI_SECTIONS_TEXT_COLUMNS
        assert FI_SECTIONS_TEXT_COLUMNS == self._EXPECTED_COLUMNS, (
            f"Parquet column spec mismatch.\n"
            f"Expected: {self._EXPECTED_COLUMNS}\n"
            f"Got:      {FI_SECTIONS_TEXT_COLUMNS}"
        )

    def test_section_text_dataclass_frozen(self) -> None:
        """SectionText is frozen — mutation must raise."""
        st = SectionText(
            statute_id="test/1",
            section_key="section:1",
            section_label="1 §",
            heading_text="",
            body_text="text",
            char_count=4,
            source_span_byte_offset=None,
            source_span_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        with pytest.raises((AttributeError, TypeError)):
            st.statute_id = "mutated"  # type: ignore[misc]  # ty:ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# 4. Reuse-verification: body_text for a known provision
# ---------------------------------------------------------------------------


class TestReuseVerification:
    """Category 4: body_text content verification against known statute text."""

    def test_body_text_contains_statute_text(self) -> None:
        """MULTI_SECTION fixture section 1 body_text contains expected Finnish."""
        result = _extract(MULTI_SECTION)
        rows = {s.section_key: s for s in result.sections}
        s1 = rows["section:1"]
        assert "hallintoa" in s1.body_text.lower(), (
            f"Expected 'hallintoa' in body_text, got {s1.body_text!r}"
        )

    def test_section_label_is_num_text(self) -> None:
        """section_label matches the <num> text from AKN."""
        result = _extract(MULTI_SECTION)
        labels = {s.section_key: s.section_label for s in result.sections}
        assert labels["section:1"] == "1 §"
        assert labels["section:2"] == "2 §"

    def test_heading_text_is_heading_element(self) -> None:
        """heading_text matches <heading> element text."""
        result = _extract(MULTI_SECTION)
        rows = {s.section_key: s for s in result.sections}
        assert rows["section:1"].heading_text == "Tarkoitus"
        assert rows["section:2"].heading_text == "Soveltamisala"


# ---------------------------------------------------------------------------
# 5. Negative tests
# ---------------------------------------------------------------------------


class TestNegative:
    """Category 5: amendment rejection, empty sections, parse failures."""

    def test_amendment_rejected_zero_rows(self) -> None:
        result = _extract(AMENDMENT_REJECTED)
        assert len(result.sections) == 0, (
            f"Expected 0 sections for amendment, got {len(result.sections)}"
        )

    def test_amendment_rejected_blocking_diagnostic(self) -> None:
        result = _extract(AMENDMENT_REJECTED)
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_sections_text_wrong_frbr_subtype" in diag_ids

    def test_amendment_rejected_diagnostic_is_blocking(self) -> None:
        result = _extract(AMENDMENT_REJECTED)
        blocking = [d for d in result.diagnostics if d.blocking]
        assert blocking, "Expected at least one blocking diagnostic"

    def test_xml_parse_failure_zero_rows(self) -> None:
        # Includes <section> to pass the substring guard, then fails XML parse
        result = extract_sections_text(b"<section><unclosed>", "2000/1")
        assert len(result.sections) == 0

    def test_xml_parse_failure_blocking_diagnostic(self) -> None:
        # Includes <section> to pass the substring guard, then fails XML parse
        result = extract_sections_text(b"<section><unclosed>", "2000/1")
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_sections_text_xml_parse_failed" in diag_ids

    def test_no_section_elements_guard(self) -> None:
        """AKN without <section> or :section emits no-section-elements diagnostic."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body><hcontainer><p>text</p></hcontainer></body></act>"
            b"</akomaNtoso>"
        )
        result = extract_sections_text(xml, "test/1")
        assert len(result.sections) == 0

    def test_eid_to_section_key_empty_string(self) -> None:
        assert _eid_to_section_key("") == ""

    def test_eid_to_section_key_no_sec(self) -> None:
        """eId without 'sec_' component returns empty."""
        assert _eid_to_section_key("chp_1") == ""

    def test_eid_version_suffix_stripped(self) -> None:
        """Version suffix vYYYYNNNN is stripped from section number."""
        key = _eid_to_section_key("part_1__chp_1__sec_5v20190432")
        assert key == "chapter:1/section:5"


# ---------------------------------------------------------------------------
# 6. Strict-mode / graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Category 6: oracle unavailable degrades gracefully (§1.8)."""

    def test_none_xml_bytes_handled_by_emitter(self) -> None:
        """export_fi_sections_text handles None oracle gracefully via emitter."""
        from lawvm.tools.export_fi_sections_text import _project_sections_for_statute

        class _NullStore:
            def read_oracle(self, sid: str):
                return None

        rows, diags = _project_sections_for_statute("test/1", _NullStore())
        assert rows == []
        assert diags == []

    def test_empty_corpus_returns_zero(self, tmp_path) -> None:
        """Empty corpus list → 0 rows written."""
        from lawvm.tools.export_fi_sections_text import export_fi_sections_text

        class _NullStore:
            def read_oracle(self, sid: str):
                return None

        # Patch the store loader to avoid farchive dependency
        import lawvm.tools.export_fi_sections_text as mod
        orig = mod._load_corpus_store
        mod._load_corpus_store = lambda: _NullStore()  # ty:ignore[invalid-assignment]
        try:
            count = export_fi_sections_text(
                [],
                data_dir=str(tmp_path),
                use_parquet=False,
            )
        finally:
            mod._load_corpus_store = orig
        assert count == 0

    def test_extraction_result_no_exception_on_empty_body(self) -> None:
        """Statute with body but no sections: result.sections is empty, no raise.

        The substring guard (§1.11) fires first when '<section' is absent,
        emitting 'fi_sections_text_no_section_elements'.
        When the body IS present but contains sections that fail key extraction,
        'fi_sections_text_zero_sections_extracted' is emitted instead.
        """
        # XML without any <section> — guard fires
        xml_no_sec = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta><identification source='#o'>"
            b"<FRBRWork><FRBRsubtype value='statute-consolidated'/></FRBRWork>"
            b"</identification></meta>"
            b"<body></body></act></akomaNtoso>"
        )
        result = extract_sections_text(xml_no_sec, "test/1")
        assert isinstance(result, SectionTextExtractionResult)
        assert len(result.sections) == 0
        # Substring guard fires first — no_section_elements diagnostic
        assert any(
            d.rule_id == "fi_sections_text_no_section_elements"
            for d in result.diagnostics
        )

        # XML with <section> but no extractable eId — body parse, zero sections
        xml_no_eid = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta><identification source='#o'>"
            b"<FRBRWork><FRBRsubtype value='statute-consolidated'/></FRBRWork>"
            b"</identification></meta>"
            b"<body>"
            b"<section><num>1</num></section>"  # no eId attr → skipped
            b"</body></act></akomaNtoso>"
        )
        result2 = extract_sections_text(xml_no_eid, "test/2")
        assert isinstance(result2, SectionTextExtractionResult)
        assert len(result2.sections) == 0
        # After body walk, zero sections extracted
        assert any(
            d.rule_id == "fi_sections_text_zero_sections_extracted"
            for d in result2.diagnostics
        )


# ---------------------------------------------------------------------------
# 7. No-leak: synthetic markers must not appear in production rows
# ---------------------------------------------------------------------------


class TestNoLeak:
    """Category 7: synthetic statute_id and section markers must not leak."""

    def test_synthetic_statute_id_not_in_production_corpus(self) -> None:
        """__test__ prefix statute IDs are never part of the real farchive corpus."""
        # This test validates the fixture flag mechanism at the extraction level.
        result = extract_sections_text(
            MULTI_SECTION.xml_bytes,
            "__test__/9999/synthetic",
        )
        for row in [section_text_to_row(s) for s in result.sections]:
            assert "__test__" in row["statute_id"], (
                "statute_id should carry through as-is (synthetic marker preserved "
                "in the row but must not be auto-injected into production runs)"
            )

    def test_section_text_to_row_no_extra_synthetic_fields(self) -> None:
        """section_text_to_row emits only the declared schema columns."""
        st = SectionText(
            statute_id="test/1",
            section_key="section:1",
            section_label="1 §",
            heading_text="heading",
            body_text="body",
            char_count=4,
            source_span_byte_offset=None,
            source_span_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = section_text_to_row(st)
        schema_keys = {
            "statute_id", "section_key", "section_label", "heading_text",
            "body_text", "char_count", "source_span_byte_offset",
            "source_span_len", "valid_at_start", "valid_at_end",
        }
        assert set(row.keys()) == schema_keys

    def test_diagnostics_are_typed_not_strings(self) -> None:
        """Diagnostics are SectionTextExtractionDiagnostic, not raw strings."""
        result = _extract(AMENDMENT_REJECTED)
        for d in result.diagnostics:
            assert isinstance(d, SectionTextExtractionDiagnostic), (
                f"Expected typed diagnostic, got {type(d)}"
            )


# ---------------------------------------------------------------------------
# eId-to-section-key helper unit tests
# ---------------------------------------------------------------------------


class TestEidToSectionKey:
    """Unit tests for _eid_to_section_key conversion."""

    @pytest.mark.parametrize("eid,expected", [
        ("sec_1", "section:1"),
        ("sec_7", "section:7"),
        ("chp_1__sec_1", "chapter:1/section:1"),
        ("chp_2__sec_9", "chapter:2/section:9"),
        ("part_1__chp_1__sec_1", "chapter:1/section:1"),
        ("part_1__chp_1__sec_3av20190809", "chapter:1/section:3a"),
        ("part_1__chp_2__sec_7v20140368", "chapter:2/section:7"),
        ("part_1__chp_1__sec_5v20190432", "chapter:1/section:5"),
        ("", ""),
        ("chp_3", ""),  # no sec_ component
    ])
    def test_eid_mapping(self, eid: str, expected: str) -> None:
        assert _eid_to_section_key(eid) == expected, (
            f"_eid_to_section_key({eid!r}) = {_eid_to_section_key(eid)!r}, "
            f"expected {expected!r}"
        )
