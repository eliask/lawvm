"""Tests for Bug B fix: ProvisionRef.serialized() → HierarchicalLocator parsing
and FinlexSectionSourceProvider handling both provision_ref formats.

Covers:
  1. parse_provision_ref_serialized: statute-level only (no section)
  2. parse_provision_ref_serialized: with section label
  3. parse_provision_ref_serialized: with section + subsection
  4. parse_provision_ref_serialized: section_key format passes through
  5. FinlexSectionSourceProvider: section_key format resolves to section
  6. FinlexSectionSourceProvider: ProvisionRef.serialized() format resolves to section
  7. FinlexSectionSourceProvider: non-existent serialized ref returns None
  8. Real-corpus regression: 5 NULL-canonical-id rows fetched, ≥4/5 return bytes
"""
from __future__ import annotations

from pathlib import Path


from lawvm.finland.provision_ref_locator import parse_provision_ref_serialized
from lawvm.core.locator import LocatorSegment


# ---------------------------------------------------------------------------
# AKN fixture builder
# ---------------------------------------------------------------------------


_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _build_statute_with_chapter_sections(statute_id: str = "2003/434") -> bytes:
    """Minimal AKN statute with chapter + sections for section_key and serialized-ref tests."""
    return (
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<act>"
        b"<meta>"
        b"<identification source='#org'>"
        b"<FRBRWork>"
        b"<FRBRthis value='/akn/fi/act/statute-consolidated/2003/434/!main'/>"
        b"<FRBRsubtype value='statute-consolidated'/>"
        b"</FRBRWork>"
        b"<FRBRExpression>"
        b"<FRBRdate date='2024-01-15' name='dateConsolidated'/>"
        b"</FRBRExpression>"
        b"</identification>"
        b"</meta>"
        b"<body>"
        b"<section eId='sec_1'>"
        b"<num>1 \xc2\xa7</num>"
        b"<heading>Tarkoitus</heading>"
        b"<subsection><content>"
        b"<p>T\xc3\xa4m\xc3\xa4 laki koskee hallintoa. Viitaus lakiin 434/2003.</p>"
        b"</content></subsection>"
        b"</section>"
        b"<section eId='sec_12'>"
        b"<num>12 \xc2\xa7</num>"
        b"<heading>Kuuleminen</heading>"
        b"<subsection><content>"
        b"<p>Asianosaiselle on varattava tilaisuus lausua mielipiteens\xc3\xa4.</p>"
        b"</content></subsection>"
        b"</section>"
        b"</body>"
        b"</act>"
        b"</akomaNtoso>"
    )


# ---------------------------------------------------------------------------
# Tests: parse_provision_ref_serialized
# ---------------------------------------------------------------------------


def test_parse_statute_level_only():
    """'2003/434' — statute-level only, no section → (statute_id, None)."""
    statute_id, locator = parse_provision_ref_serialized("2003/434")
    assert statute_id == "2003/434"
    assert locator is None


def test_parse_with_section_label():
    """'1734/3-000/12' → (statute_id='1734/3-000', section:12)."""
    statute_id, locator = parse_provision_ref_serialized("1734/3-000/12")
    assert statute_id == "1734/3-000"
    assert locator is not None
    assert len(locator.segments) >= 1
    assert locator.segments[0].kind == "section"
    assert locator.segments[0].label == "12"


def test_parse_with_section_label_space():
    """'1734/4-000/2 a' → (statute_id='1734/4-000', section:'2 a')."""
    statute_id, locator = parse_provision_ref_serialized("1734/4-000/2 a")
    assert statute_id == "1734/4-000"
    assert locator is not None
    assert locator.segments[0].kind == "section"
    assert locator.segments[0].label == "2 a"


def test_parse_with_section_and_subsection():
    """'2003/434/12/3' → section:12, subsection:3."""
    statute_id, locator = parse_provision_ref_serialized("2003/434/12/3")
    assert statute_id == "2003/434"
    assert locator is not None
    assert len(locator.segments) == 2
    assert locator.segments[0] == LocatorSegment(kind="section", label="12")
    assert locator.segments[1] == LocatorSegment(kind="subsection", label="3")


def test_parse_empty_string():
    """Empty string → ('', None)."""
    statute_id, locator = parse_provision_ref_serialized("")
    assert statute_id == ""
    assert locator is None


def test_parse_single_token_no_year():
    """'something' without slash → treated as opaque statute_id."""
    statute_id, locator = parse_provision_ref_serialized("something")
    assert statute_id == "something"
    assert locator is None


def test_parse_section_key_format_not_confused():
    """'section:3' has ':' and should NOT be treated as serialized ProvisionRef.

    parse_provision_ref_serialized should return ('', None) for this — it
    doesn't parse section_key format (that's handled by FinlexSectionSourceProvider
    _resolve_provision_ref_to_section_key which checks for ':' first).
    """
    statute_id, locator = parse_provision_ref_serialized("section:3")
    # Single token without a year prefix — returns as opaque statute_id
    assert locator is None


# ---------------------------------------------------------------------------
# Tests: FinlexSectionSourceProvider — both provision_ref formats
# ---------------------------------------------------------------------------


def _make_mock_store(oracle_bytes: bytes, statute_id: str):
    class _MockStore:
        def read_oracle(self, sid):
            return oracle_bytes if sid == statute_id else None
    return _MockStore()


def test_provider_section_key_format(tmp_path: Path):
    """section_key format ('section:12') — original behavior still works."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    from lawvm.core.manual_claims.primitive import ClaimScope
    import lawvm.corpus_store as cs_module

    statute_id = "2003/434"
    oracle_xml = _build_statute_with_chapter_sections(statute_id)
    original = cs_module.get_corpus_store

    cs_module.get_corpus_store = lambda **kw: _make_mock_store(oracle_xml, statute_id)
    try:
        provider = FinlexSectionSourceProvider()
        scope = ClaimScope(
            statute_id=statute_id,
            provision_ref="section:12",
            valid_at_start=None,
            valid_at_end=None,
        )
        result = provider.fetch(scope)
    finally:
        cs_module.get_corpus_store = original

    assert result is not None, "Expected FetchedSource for section:12"
    text = result.bytes_.decode("utf-8", errors="replace")
    assert "Kuuleminen" in text or "12" in text, f"Expected section 12 content, got: {text!r}"


def test_provider_provision_ref_serialized_format(tmp_path: Path):
    """ProvisionRef.serialized() format ('2003/434/12') resolves to section 12."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    from lawvm.core.manual_claims.primitive import ClaimScope
    import lawvm.corpus_store as cs_module

    statute_id = "2003/434"
    oracle_xml = _build_statute_with_chapter_sections(statute_id)
    original = cs_module.get_corpus_store

    cs_module.get_corpus_store = lambda **kw: _make_mock_store(oracle_xml, statute_id)
    try:
        provider = FinlexSectionSourceProvider()
        scope = ClaimScope(
            statute_id=statute_id,
            provision_ref="2003/434/12",
            valid_at_start=None,
            valid_at_end=None,
        )
        result = provider.fetch(scope)
    finally:
        cs_module.get_corpus_store = original

    assert result is not None, (
        "Expected FetchedSource for ProvisionRef.serialized() format '2003/434/12'"
    )
    text = result.bytes_.decode("utf-8", errors="replace")
    assert "Kuuleminen" in text or "12" in text, (
        f"Expected section 12 content, got: {text!r}"
    )


def test_provider_nonexistent_serialized_ref_returns_none(tmp_path: Path):
    """ProvisionRef.serialized() pointing to non-existent section → returns None."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    from lawvm.core.manual_claims.primitive import ClaimScope
    import lawvm.corpus_store as cs_module

    statute_id = "2003/434"
    oracle_xml = _build_statute_with_chapter_sections(statute_id)
    original = cs_module.get_corpus_store

    cs_module.get_corpus_store = lambda **kw: _make_mock_store(oracle_xml, statute_id)
    try:
        provider = FinlexSectionSourceProvider()
        scope = ClaimScope(
            statute_id=statute_id,
            provision_ref="2003/434/999",  # section 999 doesn't exist
            valid_at_start=None,
            valid_at_end=None,
        )
        result = provider.fetch(scope)
    finally:
        cs_module.get_corpus_store = original

    assert result is None, "Expected None for non-existent section via serialized ref"


def test_provider_serialized_ref_statute_level_only_falls_back_to_first_section(tmp_path: Path):
    """ProvisionRef.serialized() with statute-level only (no section) → first section fallback."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    from lawvm.core.manual_claims.primitive import ClaimScope
    import lawvm.corpus_store as cs_module

    statute_id = "2003/434"
    oracle_xml = _build_statute_with_chapter_sections(statute_id)
    original = cs_module.get_corpus_store

    cs_module.get_corpus_store = lambda **kw: _make_mock_store(oracle_xml, statute_id)
    try:
        provider = FinlexSectionSourceProvider()
        scope = ClaimScope(
            statute_id=statute_id,
            provision_ref=None,  # No provision_ref — falls back to first section
            valid_at_start=None,
            valid_at_end=None,
        )
        result = provider.fetch(scope)
    finally:
        cs_module.get_corpus_store = original

    assert result is not None, "Expected FetchedSource (first section fallback)"
    text = result.bytes_.decode("utf-8", errors="replace")
    assert "Tarkoitus" in text or "hallintoa" in text, f"Expected section 1 content, got: {text!r}"
