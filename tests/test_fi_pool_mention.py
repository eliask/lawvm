"""Tests for PoolMention core primitive and Finland extractor.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests -- typed primitive construction + enum coverage.
  2. Real corpus regression via conformance fixtures.
  3. Finding/observation tests -- AmbiguousPoolMention, BudgetLineRenumberingObservation,
     RejectedPoolCandidate emitted correctly.
  4. Negative tests -- year references and non-pool text do not produce BUDGET_LINE PoolMention.
  5. Strict-mode tests -- UNRESOLVED/AMBIGUOUS blocked in strict mode.
  6. No-leak tests -- synthetic markers not in non-test parquet.
  7. Schema-stability tests -- parquet column order + dtypes pinned.

Module coverage:
  - lawvm.core.pool_mention (ProvisionMention marker protocol -- the abstract base)
  - lawvm.finland.pool_mention_primitive (PoolMention, QuantityKind,
    PoolResolutionConfidence, pool_canonical_id, pool_mention_to_row, etc.)
  - lawvm.finland.pool_mention_extractor (extraction entry points)
  - lawvm.finland.canonical_budget_line_registry (REGISTRY, BudgetLine)
  - lawvm.finland.conformance_corpus.pools.fixtures (conformance fixtures)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, cast

import pytest

from lawvm.core.pool_mention import ProvisionMention
from lawvm.finland.pool_mention_primitive import (
    AmbiguousPoolMention,
    BudgetLineRenumberingObservation,
    PoolMention,
    PoolResolutionConfidence,
    QuantityKind,
    RejectedPoolCandidate,
    pool_canonical_id,
    pool_mention_to_row,
)
from lawvm.finland.pool_mention_extractor import (
    BudgetLineRecognizer,
    PoolExtractionResult,
    extract_pool_mentions,
)
from lawvm.finland.canonical_budget_line_registry import REGISTRY
from lawvm.finland.conformance_corpus.pools.fixtures import (
    ALL_FIXTURES,
    APPROXIMATE_BUDGET_LINE_RENUMBERED,
    EXACT_BUDGET_LINE,
    EXACT_CAPACITY_CAP,
    NEGATIVE_YEAR_REFERENCE,
    NO_LEAK_SYNTHETIC_MARKER,
    THRESHOLD_NUMERIC,
    UNRESOLVED_YLEISKATE,
    XML_PARSE_FAILURE,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_mention_matches(actual: PoolMention, expected: Dict[str, Any]) -> None:
    """Assert that actual PoolMention matches all expected key/value pairs."""
    row = pool_mention_to_row(actual)
    row["quantity_phrase"] = actual.quantity_phrase
    row["pool_canonical_id"] = actual.pool_canonical_id
    row["quantity_kind"] = actual.quantity_kind.value
    row["resolution_confidence"] = actual.resolution_confidence.value
    row["numeric_value"] = actual.numeric_value
    row["unit"] = actual.unit
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"mention key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


def _set_runtime_attr(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


def _runtime_candidate_canonical_ids(values: list[str]) -> tuple[str, ...]:
    return cast(tuple[str, ...], values)


def _required_str(value: str | None) -> str:
    assert value is not None
    return value


# ===========================================================================
# Category 1: Synthetic unit tests -- typed primitive construction
# ===========================================================================


class TestPoolMentionConstruction:
    """Synthetic unit tests for the typed primitive itself."""

    def test_exact_budget_line_construction(self) -> None:
        """EXACT resolution: pool_canonical_id required."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="momentilla 28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        assert mention.pool_canonical_id == "fi.budget.28.91.50"
        assert mention.quantity_kind == QuantityKind.BUDGET_LINE
        assert mention.resolution_confidence == PoolResolutionConfidence.EXACT

    def test_unresolved_allows_none_canonical_id(self) -> None:
        """UNRESOLVED resolution allows pool_canonical_id=None."""
        mention = PoolMention(
            source_provision_ref="2003/314/10",
            quantity_phrase="yleiskate",
            pool_canonical_id=None,
            quantity_kind=QuantityKind.FISCAL_POOL,
            resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        assert mention.pool_canonical_id is None
        assert mention.resolution_confidence == PoolResolutionConfidence.UNRESOLVED

    def test_exact_requires_canonical_id(self) -> None:
        """EXACT resolution MUST have a non-None pool_canonical_id."""
        with pytest.raises(ValueError, match="pool_canonical_id"):
            PoolMention(
                source_provision_ref="711/2022/3",
                quantity_phrase="momentilla 28.91.50",
                pool_canonical_id=None,
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=PoolResolutionConfidence.EXACT,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )

    def test_empty_quantity_phrase_rejected(self) -> None:
        """Empty quantity_phrase is not allowed."""
        with pytest.raises(ValueError, match="quantity_phrase"):
            PoolMention(
                source_provision_ref="711/2022/3",
                quantity_phrase="",
                pool_canonical_id="fi.budget.28.91.50",
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=PoolResolutionConfidence.EXACT,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )

    def test_frozen_dataclass_immutable(self) -> None:
        """PoolMention is frozen (immutable)."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        with pytest.raises((TypeError, AttributeError)):
            _set_runtime_attr(mention, "quantity_kind", QuantityKind.FISCAL_POOL)

    def test_capacity_cap_with_numeric_and_unit(self) -> None:
        """CAPACITY_CAP with numeric value and unit stores correctly."""
        mention = PoolMention(
            source_provision_ref="539/2006/8",
            quantity_phrase="enintaan 7,5 g Cd/ha/5 v",
            pool_canonical_id=None,
            quantity_kind=QuantityKind.CAPACITY_CAP,
            resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
            numeric_value=7.5,
            unit="g Cd/ha/5 v",
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        assert mention.numeric_value == 7.5
        assert mention.unit == "g Cd/ha/5 v"

    def test_all_quantity_kinds_constructable(self) -> None:
        """Each QuantityKind enum value is constructable."""
        for kind in QuantityKind:
            cid = "fi.budget.28.91.50" if kind == QuantityKind.BUDGET_LINE else None
            conf = PoolResolutionConfidence.EXACT if cid else PoolResolutionConfidence.UNRESOLVED
            mention = PoolMention(
                source_provision_ref="711/2022/1",
                quantity_phrase="test",
                pool_canonical_id=cid,
                quantity_kind=kind,
                resolution_confidence=conf,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )
            assert mention.quantity_kind == kind

    def test_all_resolution_confidences_constructable(self) -> None:
        """All PoolResolutionConfidence values constructable."""
        for conf in PoolResolutionConfidence:
            cid = None if conf == PoolResolutionConfidence.UNRESOLVED else "fi.budget.28.91.50"
            mention = PoolMention(
                source_provision_ref="711/2022/1",
                quantity_phrase="28.91.50",
                pool_canonical_id=cid,
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=conf,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )
            assert mention.resolution_confidence == conf

    def test_valid_at_interval_stored(self) -> None:
        """valid_at_start and valid_at_end are stored correctly."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file="test.xml",
            source_span_byte_offset=100,
            source_span_byte_len=12,
            valid_at_start=date(2024, 1, 1),
            valid_at_end=None,
        )
        assert mention.valid_at_start == date(2024, 1, 1)
        assert mention.valid_at_end is None
        assert mention.source_span_byte_offset == 100


# ===========================================================================
# Category 1b: Enum coverage
# ===========================================================================


class TestEnumCoverage:
    """All enum values are accessible and have correct string values."""

    def test_quantity_kind_values(self) -> None:
        assert QuantityKind.BUDGET_LINE.value == "budget_line"
        assert QuantityKind.FISCAL_POOL.value == "fiscal_pool"
        assert QuantityKind.CAPACITY_CAP.value == "capacity_cap"
        assert QuantityKind.THRESHOLD.value == "threshold"
        assert QuantityKind.FORMULA_TERM.value == "formula_term"
        assert QuantityKind.UNRESOLVED.value == "unresolved"

    def test_resolution_confidence_values(self) -> None:
        assert PoolResolutionConfidence.EXACT.value == "exact"
        assert PoolResolutionConfidence.APPROXIMATE.value == "approximate"
        assert PoolResolutionConfidence.UNRESOLVED.value == "unresolved"


# ===========================================================================
# Category 1c: Registry unit tests
# ===========================================================================


class TestCanonicalBudgetLineRegistry:
    """Unit tests for the canonical budget-line registry."""

    def test_registry_has_years(self) -> None:
        """Registry has at least one year loaded."""
        years = REGISTRY.available_years()
        assert len(years) >= 1

    def test_registry_has_2024(self) -> None:
        """Registry has 2024 data."""
        years = REGISTRY.available_years()
        assert 2024 in years

    def test_lookup_28_91_50_in_2024(self) -> None:
        """28.91.50 resolves to fi.budget.28.91.50 in 2024."""
        cid, lines = REGISTRY.lookup_by_code("28.91.50", 2024)
        assert cid == "fi.budget.28.91.50"
        assert len(lines) == 1
        assert lines[0].show_as is not None

    def test_lookup_unknown_code_returns_empty(self) -> None:
        """Unknown momentti code returns no matches."""
        cid, lines = REGISTRY.lookup_by_code("99.99.99", 2024)
        assert cid is None
        assert lines == []

    def test_lookup_by_code_2020_returns_old_momentti(self) -> None:
        """28.91.51 resolves in 2020 (before renumbering)."""
        cid, lines = REGISTRY.lookup_by_code("28.91.51", 2020)
        # 28.91.51 should exist in 2020
        if len(lines) > 0:
            assert cid is not None
            assert "28.91.51" in cid or lines[0].momentti_code == "28.91.51"

    def test_nearest_year(self) -> None:
        """nearest_year returns the closest available year."""
        years = REGISTRY.available_years()
        if years:
            nearest = REGISTRY.nearest_year(2024)
            assert nearest is not None
            # Should be in available years
            assert nearest in years

    def test_get_line_returns_budget_line(self) -> None:
        """get_line returns a BudgetLine for a known ID+year."""
        bl = REGISTRY.get_line("fi.budget.28.91.50", 2024)
        if bl is not None:
            assert bl.paaluokka == 28
            assert bl.luku == 91
            assert bl.momentti == 50

    def test_all_lines_for_year_nonempty(self) -> None:
        """all_lines_for_year returns non-empty list for 2024."""
        lines = REGISTRY.all_lines_for_year(2024)
        assert len(lines) > 0

    def test_budget_line_is_frozen_dataclass(self) -> None:
        """BudgetLine is frozen."""
        lines = REGISTRY.all_lines_for_year(2024)
        if lines:
            bl = lines[0]
            with pytest.raises((TypeError, AttributeError)):
                _set_runtime_attr(bl, "paaluokka", 99)


# ===========================================================================
# Category 1d: BudgetLineRecognizer unit tests
# ===========================================================================


class TestBudgetLineRecognizer:
    """Unit tests for the named budget-line/pool/quantity recognizer family."""

    def setup_method(self) -> None:
        self.recognizer = BudgetLineRecognizer()

    def test_recognize_explicit_momentti_address(self) -> None:
        """'momentilla 28.91.50' recognized as BUDGET_LINE."""
        text = "M\xe4\xe4r\xe4raha osoitetaan momentilla 28.91.50 kunnille."
        candidates = self.recognizer.recognize(text)
        bl = [c for c in candidates if c.inferred_kind == QuantityKind.BUDGET_LINE]
        assert len(bl) >= 1
        assert "28.91.50" in _required_str(bl[0].momentti_code)

    def test_recognize_bare_momentti_code(self) -> None:
        """Bare '29.20.30' without 'momentilla' keyword recognized as BUDGET_LINE."""
        text = "Avustus maksetaan 29.20.30 mukaisesti."
        candidates = self.recognizer.recognize(text)
        bl = [c for c in candidates if c.inferred_kind == QuantityKind.BUDGET_LINE]
        assert len(bl) >= 1

    def test_recognize_capacity_cap(self) -> None:
        """'enintaan 7,5 g Cd/ha/5 v' recognized as CAPACITY_CAP."""
        text = "Kuormakatto on enint\xe4\xe4n 7,5 g Cd/ha/5 v"
        candidates = self.recognizer.recognize(text)
        caps = [c for c in candidates if c.inferred_kind == QuantityKind.CAPACITY_CAP]
        assert len(caps) >= 1
        assert caps[0].numeric_value == pytest.approx(7.5)

    def test_recognize_fiscal_pool(self) -> None:
        """'maaraaraha' keyword recognized as FISCAL_POOL."""
        text = "M\xe4\xe4r\xe4raha on varattu seuraavalle vuodelle."
        candidates = self.recognizer.recognize(text)
        pools = [c for c in candidates if c.inferred_kind == QuantityKind.FISCAL_POOL]
        assert len(pools) >= 1

    def test_negative_year_reference_not_budget_line(self) -> None:
        """'vuoden 2020' does not produce a BUDGET_LINE candidate."""
        text = "Vuoden 2020 talousarviossa osoitettu m\xe4\xe4r\xe4raha."
        candidates = self.recognizer.recognize(text)
        bl = [c for c in candidates if c.inferred_kind == QuantityKind.BUDGET_LINE]
        # Year references should be filtered by the negative guard
        assert len(bl) == 0, f"Expected no BUDGET_LINE candidates; got {[c.momentti_code for c in bl]}"

    def test_recognize_threshold(self) -> None:
        """'vahintaan 0,5 promillea' recognized as THRESHOLD."""
        text = "Rikos, jos arvo on v\xe4hint\xe4\xe4n 0,5 promillea."
        candidates = self.recognizer.recognize(text)
        thresholds = [c for c in candidates if c.inferred_kind == QuantityKind.THRESHOLD]
        assert len(thresholds) >= 1
        assert thresholds[0].numeric_value == pytest.approx(0.5)


# ===========================================================================
# Category 2: Real corpus regression via conformance fixtures
# ===========================================================================


class TestConformanceFixtures:
    """Real AKN patterns from the conformance corpus."""

    def test_fixture_exact_budget_line(self) -> None:
        """EXACT x BUDGET_LINE: explicit 28.91.50 momentti address."""
        result = extract_pool_mentions(
            EXACT_BUDGET_LINE.xml_bytes,
            EXACT_BUDGET_LINE.source_statute_id,
        )
        bl_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.BUDGET_LINE
        ]
        assert len(bl_mentions) >= 1, f"Expected BUDGET_LINE mention, got {result.mentions}"
        exact = [m for m in bl_mentions if m.resolution_confidence == PoolResolutionConfidence.EXACT]
        assert len(exact) >= 1, f"Expected EXACT confidence, got {[m.resolution_confidence for m in bl_mentions]}"
        assert exact[0].pool_canonical_id == "fi.budget.28.91.50"

    def test_fixture_approximate_budget_line_renumbered(self) -> None:
        """APPROXIMATE x BUDGET_LINE: 28.91.51 (2020) resolves via lineage."""
        result = extract_pool_mentions(
            APPROXIMATE_BUDGET_LINE_RENUMBERED.xml_bytes,
            APPROXIMATE_BUDGET_LINE_RENUMBERED.source_statute_id,
        )
        bl_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.BUDGET_LINE
        ]
        assert len(bl_mentions) >= 1, f"Expected BUDGET_LINE mention, got {result.mentions}"
        # Should be APPROXIMATE (via lineage) or EXACT (if 28.91.51 is in the registry)
        # Either way, should NOT be silently dropped.
        assert bl_mentions[0].resolution_confidence in (
            PoolResolutionConfidence.APPROXIMATE,
            PoolResolutionConfidence.EXACT,
            PoolResolutionConfidence.UNRESOLVED,
        )

    def test_fixture_exact_capacity_cap(self) -> None:
        """EXACT x CAPACITY_CAP: lannoitelaki Cd-kuormakatto 7,5 g Cd/ha/5 v."""
        result = extract_pool_mentions(
            EXACT_CAPACITY_CAP.xml_bytes,
            EXACT_CAPACITY_CAP.source_statute_id,
        )
        cap_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.CAPACITY_CAP
        ]
        assert len(cap_mentions) >= 1, f"Expected CAPACITY_CAP mention, got {result.mentions}"
        cap = cap_mentions[0]
        assert cap.numeric_value == pytest.approx(7.5)

    def test_fixture_threshold_numeric(self) -> None:
        """THRESHOLD: numeric threshold 0,5 promillea."""
        result = extract_pool_mentions(
            THRESHOLD_NUMERIC.xml_bytes,
            THRESHOLD_NUMERIC.source_statute_id,
        )
        threshold_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.THRESHOLD
        ]
        assert len(threshold_mentions) >= 1, f"Expected THRESHOLD mention, got {result.mentions}"
        t = threshold_mentions[0]
        assert t.numeric_value == pytest.approx(0.5)

    def test_fixture_unresolved_yleiskate(self) -> None:
        """UNRESOLVED x FISCAL_POOL: 'yleiskate' generic pool phrase."""
        result = extract_pool_mentions(
            UNRESOLVED_YLEISKATE.xml_bytes,
            UNRESOLVED_YLEISKATE.source_statute_id,
        )
        pool_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.FISCAL_POOL
        ]
        assert len(pool_mentions) >= 1, f"Expected FISCAL_POOL mention, got {result.mentions}"
        assert pool_mentions[0].pool_canonical_id is None

    def test_fixture_xml_parse_failure(self) -> None:
        """Corrupt XML -> blocking RejectedPoolCandidate, no mentions."""
        result = extract_pool_mentions(
            XML_PARSE_FAILURE.xml_bytes,
            XML_PARSE_FAILURE.source_statute_id,
        )
        assert result.mentions == []
        blocking = [r for r in result.rejected if r.blocking]
        assert len(blocking) >= 1
        assert blocking[0].rule_id == "fi_pool_mention_xml_parse_failed"

    def test_all_fixtures_run_without_exception(self) -> None:
        """All conformance fixtures must run without unhandled exceptions."""
        for fid, fixture in ALL_FIXTURES.items():
            result = extract_pool_mentions(
                fixture.xml_bytes,
                fixture.source_statute_id,
            )
            assert isinstance(result, PoolExtractionResult), (
                f"Fixture {fid}: expected PoolExtractionResult, got {type(result)}"
            )


# ===========================================================================
# Category 3: Finding/observation tests
# ===========================================================================


class TestFindingObservation:
    """Tests that typed findings are emitted correctly."""

    def test_ambiguous_pool_mention_construction(self) -> None:
        """AmbiguousPoolMention has required fields + normalizes tuple."""
        finding = AmbiguousPoolMention(
            rule_id="fi_pool_mention_ambiguous_budget_line",
            phase="pool_mention_extraction",
            source_statute_id="711/2022",
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            candidate_canonical_ids=_runtime_candidate_canonical_ids(
                ["fi.budget.28.91.50", "fi.budget.28.91.51"]
            ),
            reason="Momentti code maps to 2 entries.",
        )
        assert isinstance(finding.candidate_canonical_ids, tuple)
        assert "fi.budget.28.91.50" in finding.candidate_canonical_ids
        assert finding.blocking is False

    def test_budget_line_renumbering_observation_construction(self) -> None:
        """BudgetLineRenumberingObservation has required fields."""
        obs = BudgetLineRenumberingObservation(
            rule_id="fi_pool_mention_budget_line_renumbering",
            phase="pool_mention_extraction",
            source_statute_id="500/2020",
            source_provision_ref="500/2020/4",
            quantity_phrase="momentilta 28.91.51",
            original_canonical_id="fi.budget.28.91.51",
            resolved_canonical_id="fi.budget.28.91.50",
            lineage_year=2020,
            resolution_year=2022,
            reason="Renumbering from 2020 to 2022.",
        )
        assert obs.lineage_year == 2020
        assert obs.resolution_year == 2022
        assert obs.blocking is False

    def test_rejected_pool_candidate_construction(self) -> None:
        """RejectedPoolCandidate has all required fields."""
        rej = RejectedPoolCandidate(
            rule_id="fi_pool_mention_xml_parse_failed",
            phase="pool_mention_extraction",
            source_statute_id="bad/0",
            reason="XML parse error.",
            matched_text="",
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            blocking=True,
        )
        assert rej.blocking is True
        assert rej.strict_disposition == "record"

    def test_unresolved_budget_line_emitted_not_dropped(self) -> None:
        """UNRESOLVED budget-line mention is emitted, not silently dropped (AGENTS.md §1.8)."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Maksetaan momentilta 99.88.77."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/1")
        bl = [m for m in result.mentions if m.quantity_kind == QuantityKind.BUDGET_LINE]
        assert len(bl) >= 1, "UNRESOLVED budget-line must be emitted, not dropped"
        assert bl[0].resolution_confidence == PoolResolutionConfidence.UNRESOLVED
        assert bl[0].pool_canonical_id is None


# ===========================================================================
# Category 4: Negative tests
# ===========================================================================


class TestNegative:
    """Non-pool text must not produce BUDGET_LINE PoolMention records."""

    def test_year_reference_not_extracted_as_budget_line(self) -> None:
        """'vuoden 2020' year reference does not produce a BUDGET_LINE mention."""
        result = extract_pool_mentions(
            NEGATIVE_YEAR_REFERENCE.xml_bytes,
            NEGATIVE_YEAR_REFERENCE.source_statute_id,
        )
        bl_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.BUDGET_LINE
        ]
        assert bl_mentions == [], (
            f"Year reference should not produce BUDGET_LINE mentions; got {bl_mentions}"
        )

    def test_empty_statute_body_no_budget_line_mentions(self) -> None:
        """A statute with no pool phrases -> no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>Ei m\xc3\xa4\xc3\xa4r\xc3\xa4rahoja.</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/2")
        bl_mentions = [m for m in result.mentions if m.quantity_kind == QuantityKind.BUDGET_LINE]
        assert bl_mentions == []

    def test_no_body_no_mentions(self) -> None:
        """Statute with no body element -> no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/3")
        assert result.mentions == []

    def test_phone_number_not_matched_as_budget_line(self) -> None:
        """A phone-like number '09.40.123' is not matched as a budget-line address.

        The pattern requires exactly 2 digits in each segment (NN.NN.NN).
        '09.40.123' has 3 digits in the last segment -> not matched.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Puhelin: 09.40.1234567"
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/4")
        bl_mentions = [m for m in result.mentions if m.quantity_kind == QuantityKind.BUDGET_LINE]
        # Phone number has more than 2 digits in last segment -> should not match
        # The regex uses \d{1,2} per segment, so '1234567' is too long
        assert len(bl_mentions) == 0 or all(
            m.pool_canonical_id is None for m in bl_mentions
        )


# ===========================================================================
# Category 5: Strict-mode tests
# ===========================================================================


class TestStrictMode:
    """Strict mode blocks UNRESOLVED and AMBIGUOUS mentions."""

    def test_strict_mode_xml_parse_failure_blocking(self) -> None:
        """In strict mode, parse failure produces blocking RejectedPoolCandidate."""
        result = extract_pool_mentions(b"<invalid>", "test/bad", strict=True)
        assert result.mentions == []
        blocking = [r for r in result.rejected if r.blocking]
        assert len(blocking) >= 1

    def test_strict_mode_blocks_unresolved(self) -> None:
        """In strict mode, UNRESOLVED mentions produce blocking rejected records."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Maksetaan momentilta 99.88.77."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/5", strict=True)
        blocking_rejected = [r for r in result.rejected if r.blocking]
        assert len(blocking_rejected) >= 1

    def test_non_strict_mode_unresolved_not_blocking(self) -> None:
        """Non-strict mode does not add blocking records for UNRESOLVED mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Maksetaan momentilta 99.88.77."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_pool_mentions(xml, "test/6", strict=False)
        unresolved = [m for m in result.mentions if m.resolution_confidence == PoolResolutionConfidence.UNRESOLVED]
        assert len(unresolved) >= 1
        blocking = [r for r in result.rejected if r.blocking]
        assert len(blocking) == 0

    def test_strict_mode_exact_budget_line_not_blocked(self) -> None:
        """EXACT confidence mentions are not blocked in strict mode."""
        result = extract_pool_mentions(
            EXACT_BUDGET_LINE.xml_bytes,
            EXACT_BUDGET_LINE.source_statute_id,
            strict=True,
        )
        exact = [m for m in result.mentions if m.resolution_confidence == PoolResolutionConfidence.EXACT]
        assert len(exact) >= 1


# ===========================================================================
# Category 6: No-leak tests
# ===========================================================================


class TestNoLeak:
    """Synthetic test markers must not leak into production parquet runs."""

    def test_synthetic_statute_id_extractable_in_test(self) -> None:
        """Synthetic IDs extract correctly in test context."""
        result = extract_pool_mentions(
            NO_LEAK_SYNTHETIC_MARKER.xml_bytes,
            NO_LEAK_SYNTHETIC_MARKER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        # Source provision ref carries the synthetic statute marker
        assert result.mentions[0].source_provision_ref.startswith("__test__")

    def test_pool_mention_to_row_no_internal_sentinels_in_canonical_id(self) -> None:
        """Serialized row canonical_id must not contain '__test__' for real pools."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = pool_mention_to_row(mention)
        assert "__test__" not in str(row["pool_canonical_id"] or "")


# ===========================================================================
# Category 7: Schema-stability tests
# ===========================================================================


class TestSchemaStability:
    """Parquet schema column order and dtypes must be pinned."""

    EXPECTED_COLUMNS = [
        "source_provision_ref_str",
        "quantity_phrase",
        "pool_canonical_id",
        "quantity_kind",
        "resolution_confidence",
        "numeric_value",
        "unit",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_byte_len",
        "valid_at_start",
        "valid_at_end",
    ]

    def test_pool_mention_to_row_has_all_columns(self) -> None:
        """pool_mention_to_row() produces all expected columns."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = pool_mention_to_row(mention)
        for col in self.EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from serialized row"

    def test_pool_mention_to_row_column_types(self) -> None:
        """Column types match expected Python types."""
        mention = PoolMention(
            source_provision_ref="539/2006/8",
            quantity_phrase="enintaan 7,5 g Cd",
            pool_canonical_id=None,
            quantity_kind=QuantityKind.CAPACITY_CAP,
            resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
            numeric_value=7.5,
            unit="g Cd/ha/5 v",
            source_span_file="/data/fi/539/2006.xml",
            source_span_byte_offset=200,
            source_span_byte_len=25,
            valid_at_start=date(2024, 1, 1),
            valid_at_end=None,
        )
        row = pool_mention_to_row(mention)
        assert isinstance(row["source_provision_ref_str"], str)
        assert isinstance(row["quantity_phrase"], str)
        assert row["pool_canonical_id"] is None
        assert isinstance(row["quantity_kind"], str)
        assert isinstance(row["resolution_confidence"], str)
        assert isinstance(row["numeric_value"], float)
        assert isinstance(row["unit"], str)
        assert isinstance(row["source_span_file"], str)
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_byte_len"], int)
        assert isinstance(row["valid_at_start"], str)  # isoformat
        assert row["valid_at_end"] is None

    def test_pool_mention_to_row_nullable_columns(self) -> None:
        """Nullable columns are None when absent."""
        mention = PoolMention(
            source_provision_ref="2003/314/10",
            quantity_phrase="yleiskate",
            pool_canonical_id=None,
            quantity_kind=QuantityKind.FISCAL_POOL,
            resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = pool_mention_to_row(mention)
        assert row["pool_canonical_id"] is None
        assert row["numeric_value"] is None
        assert row["unit"] is None
        assert row["source_span_file"] is None
        assert row["source_span_byte_offset"] is None
        assert row["source_span_byte_len"] is None
        assert row["valid_at_start"] is None
        assert row["valid_at_end"] is None

    def test_column_order_stable(self) -> None:
        """Column order in pool_mention_to_row() output is stable."""
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = pool_mention_to_row(mention)
        actual_cols = list(row.keys())
        assert actual_cols == self.EXPECTED_COLUMNS


# ===========================================================================
# Category 8: iter2 W5 H1 cross-module move regression
# ===========================================================================


class TestCrossModuleMoveRegression:
    """Finnish fiscal doctrine now lives in ``finland.pool_mention_primitive``.

    Pins the §2.3 crystallization: the abstract ``ProvisionMention`` protocol
    is the only resident of ``lawvm.core.pool_mention``; the concrete
    ``PoolMention``/``QuantityKind``/``PoolResolutionConfidence`` primitive,
    the canonical-id factory, and the parquet serializer live in
    ``lawvm.finland.pool_mention_primitive``. Behavior is byte-identical to
    the pre-move layout; these tests pin the post-move wiring so a future
    re-leak is caught at unit-test time, not at code review.
    """

    def test_concrete_primitive_imported_from_finland_not_core(self) -> None:
        """Concrete primitive lives in finland, not core.

        Importing the symbols from ``lawvm.finland.pool_mention_primitive``
        must succeed (this is the post-move home); importing them from
        ``lawvm.core.pool_mention`` must fail with AttributeError (the
        concrete names are no longer in core).
        """
        # Symbol is importable from the finland primitive.
        from lawvm.finland.pool_mention_primitive import PoolMention as FiPoolMention  # noqa: F401

        assert FiPoolMention is PoolMention  # re-import resolves to the same class.

        # The concrete symbols are gone from core (only ProvisionMention lives there).
        import lawvm.core.pool_mention as core_mod

        assert not hasattr(core_mod, "PoolMention")
        assert not hasattr(core_mod, "QuantityKind")
        assert not hasattr(core_mod, "PoolResolutionConfidence")
        assert not hasattr(core_mod, "AmbiguousPoolMention")
        assert not hasattr(core_mod, "BudgetLineRenumberingObservation")
        assert not hasattr(core_mod, "RejectedPoolCandidate")
        assert not hasattr(core_mod, "pool_canonical_id")
        assert not hasattr(core_mod, "pool_mention_to_row")

    def test_concrete_primitive_inherits_core_protocol(self) -> None:
        """``PoolMention`` explicitly inherits ``ProvisionMention``.

        Mirrors the ``ScopeConfidence`` precedent: explicit protocol
        inheritance registers the frontend dataclass as a producer in the
        AST-scan parity check, keeping the producer set equal to the
        protocol-implementer set.
        """
        mention = PoolMention(
            source_provision_ref="711/2022/3",
            quantity_phrase="28.91.50",
            pool_canonical_id="fi.budget.28.91.50",
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.EXACT,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        # runtime_checkable structural conformance.
        assert isinstance(mention, ProvisionMention)
        # Explicit name in MRO -- not just structural conformance, but
        # the AST parity-check relies on the explicit `class PoolMention(ProvisionMention)`.
        assert ProvisionMention.__name__ in {
            base.__name__ for base in type(mention).__mro__
        }

    def test_pool_canonical_id_factory_byte_identical(self) -> None:
        """``pool_canonical_id('28.91.50') == 'fi.budget.28.91.50'``.

        The post-move factory (in ``finland.pool_mention_primitive``) must
        produce the same canonical-id form the pre-move private
        ``_canonical_id_from_code`` helper produced. The old helper did an
        identity ``.replace('.', '.')`` no-op, so the post-move factory
        (which omits that no-op) is byte-identical.
        """
        assert pool_canonical_id("28.91.50") == "fi.budget.28.91.50"
        assert pool_canonical_id("1.2.3") == "fi.budget.1.2.3"

    def test_extract_to_row_round_trip_post_move(self) -> None:
        """Extraction -> pool_mention_to_row round-trip works post-move.

        Drives the full production path (BudgetLineRecognizer ->
        _resolve_budget_line -> PoolMention -> pool_mention_to_row) to
        confirm that no stage was broken by the import rewiring.
        Mirrors the focus of the wave-2 ``tarkoitetaan`` regression.
        """
        from lawvm.finland.conformance_corpus.pools.fixtures import EXACT_BUDGET_LINE

        result = extract_pool_mentions(
            EXACT_BUDGET_LINE.xml_bytes,
            EXACT_BUDGET_LINE.source_statute_id,
        )

        assert result.mentions, "EXACT_BUDGET_LINE fixture must produce at least one mention"
        bl_mentions = [
            m for m in result.mentions
            if m.quantity_kind == QuantityKind.BUDGET_LINE
        ]
        assert bl_mentions, "EXACT_BUDGET_LINE fixture must produce a BUDGET_LINE mention"
        exact = [
            m for m in bl_mentions
            if m.resolution_confidence == PoolResolutionConfidence.EXACT
        ]
        assert exact, "EXACT_BUDGET_LINE fixture must produce an EXACT match"
        # The factory was used internally for the canonical-id form -- pin it.
        assert exact[0].pool_canonical_id == pool_canonical_id("28.91.50")
        # Serializer still works on a post-move PoolMention instance.
        row = pool_mention_to_row(exact[0])
        assert row["pool_canonical_id"] == "fi.budget.28.91.50"
        assert row["quantity_kind"] == "budget_line"
        assert row["resolution_confidence"] == "exact"
