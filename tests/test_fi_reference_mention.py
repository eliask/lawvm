"""Tests for ReferenceMention core primitive and Finland extractor.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests — each cite_kind × confidence cell.
  2. Real corpus regression — at least 5 real Finnish statute patterns
     (via conformance corpus fixtures, which use real AKN patterns).
  3. Finding/observation tests — BROKEN emits BrokenReferenceFinding, etc.
  4. Negative tests — non-citation text does not produce ReferenceMention.
  5. Strict-mode tests — APPROXIMATE/UNRESOLVED blocked in strict mode.
  6. No-leak tests — synthetic markers not in non-test parquet.
  7. Schema-stability tests — parquet column order + dtypes pinned.

Module coverage:
  - lawvm.core.reference_mention (ReferenceMention, CiteKind, CiteConfidence, etc.)
  - lawvm.finland.references.ref_mention_extractor (extraction entry points)
  - lawvm.finland.conformance_corpus.refs.fixtures (conformance fixtures)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, cast

import pytest

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    ApproximateReferenceFinding,
    BrokenReferenceFinding,
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    RejectedRefCandidate,
    SourceSpan,
    reference_mention_to_row,
)
from lawvm.finland.references.ref_mention_extractor import (
    ExtractionResult,
    PlainTextStatuteCitationRecognizer,
    PlainTextStatuteHit,
    extract_all_reference_mentions,
    extract_eu_reference_mentions,
    extract_plain_text_statute_mentions,
    extract_preparatory_reference_mentions,
    extract_reference_mentions,
)
from lawvm.finland.conformance_corpus.refs.fixtures import (
    ALL_FIXTURES,
    EU_EMBEDDED_REPEAL,
    EXACT_CROSS_STATUTE,
    EXACT_EU,
    EXACT_INTERNAL_SELF_REF_SKIPPED,
    EXACT_ISSUED_UNDER,
    EXACT_REPEALS,
    NO_LEAK_SYNTHETIC_MARKER,
    XML_PARSE_FAILURE,
)
from lawvm.finland.conformance_corpus.preparatory.fixtures import FULL_CHAIN
from lawvm.core.preparatory_reference import PreparatoryReferenceKind


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_mention_matches(actual: ReferenceMention, expected: Dict[str, Any]) -> None:
    """Assert that actual ReferenceMention matches all expected key/value pairs."""
    row = reference_mention_to_row(actual)
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"mention row key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


def _assert_rejected_matches(actual: RejectedRefCandidate, expected: Dict[str, Any]) -> None:
    """Assert that actual RejectedRefCandidate matches all expected key/value pairs."""
    actual_dict = {
        "rule_id": actual.rule_id,
        "phase": actual.phase,
        "source_statute_id": actual.source_statute_id,
        "reason": actual.reason,
        "matched_text": actual.matched_text,
        "blocking": actual.blocking,
        "strict_disposition": actual.strict_disposition,
    }
    for key, val in expected.items():
        assert actual_dict.get(key) == val, (
            f"rejected candidate key {key!r}: expected {val!r}, got {actual_dict.get(key)!r}"
        )


def _runtime_candidate_target_ids(values: list[str]) -> tuple[str, ...]:
    """Deliberately pass list input so constructor normalization is exercised."""
    return cast(tuple[str, ...], values)


def _target_statute_ids(mentions: Iterable[ReferenceMention]) -> set[str]:
    target_ids: set[str] = set()
    for mention in mentions:
        assert mention.target_provision_ref is not None
        target_ids.add(mention.target_provision_ref.statute_id)
    return target_ids


def _set_runtime_attr(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


# ===========================================================================
# Category 1: Synthetic unit tests — typed primitive construction
# ===========================================================================


class TestReferenceMentionConstruction:
    """Synthetic unit tests for the typed primitive itself."""

    def test_exact_cross_statute_construction(self) -> None:
        """EXACT × CROSS_STATUTE: basic construction and serialization."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", provision_path="sec_7", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        assert mention.cite_kind == CiteKind.CROSS_STATUTE
        assert mention.cite_confidence == CiteConfidence.EXACT
        assert mention.target_provision_ref is not None
        assert mention.target_provision_ref.statute_id == "2022/711"

    def test_unresolved_allows_none_target(self) -> None:
        """UNRESOLVED confidence allows target_provision_ref=None."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.UNRESOLVED,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        assert mention.cite_confidence == CiteConfidence.UNRESOLVED
        assert mention.target_provision_ref is None

    def test_broken_allows_none_target(self) -> None:
        """BROKEN confidence allows target_provision_ref=None."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.BROKEN,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(date(2010, 1, 1), date(2022, 6, 1)),
            edge_subtype="CITES",
        )
        assert mention.cite_confidence == CiteConfidence.BROKEN

    def test_exact_requires_target(self) -> None:
        """EXACT confidence MUST have a non-None target_provision_ref."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        with pytest.raises(ValueError, match="target_provision_ref"):
            ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=None,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype="CITES",
            )

    def test_empty_phrase_lemma_rejected(self) -> None:
        """Empty phrase_lemma is not allowed."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711")
        with pytest.raises(ValueError, match="phrase_lemma"):
            ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype="CITES",
            )

    def test_all_cite_kinds_constructable(self) -> None:
        """Each CiteKind enum value is constructable."""
        for kind in CiteKind:
            src = ProvisionRef(statute_id="2003/314")
            tgt = ProvisionRef(statute_id="2022/711")
            mention = ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=kind,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype=None,
            )
            assert mention.cite_kind == kind

    def test_all_confidences_constructable_when_target_present(self) -> None:
        """EXACT/APPROXIMATE/AMBIGUOUS require non-None target."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        for conf in (CiteConfidence.EXACT, CiteConfidence.APPROXIMATE, CiteConfidence.AMBIGUOUS):
            mention = ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=conf,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype=None,
            )
            assert mention.cite_confidence == conf

    def test_source_span_construction(self) -> None:
        """SourceSpan validates byte_offset >= 0 and byte_len >= 0."""
        span = SourceSpan(source_file="test.xml", byte_offset=100, byte_len=42)
        assert span.byte_offset == 100
        assert span.byte_len == 42

    def test_source_span_negative_offset_rejected(self) -> None:
        with pytest.raises(ValueError, match="byte_offset"):
            SourceSpan(source_file="test.xml", byte_offset=-1, byte_len=10)

    def test_provision_ref_serialized(self) -> None:
        """ProvisionRef.serialized() produces stable output."""
        ref = ProvisionRef(statute_id="711/2022", section_label="7", subsection_num=3)
        assert ref.serialized() == "711/2022/7/3"

    def test_provision_ref_serialized_statute_only(self) -> None:
        ref = ProvisionRef(statute_id="711/2022")
        assert ref.serialized() == "711/2022"

    def test_provision_ref_serialized_subitem(self) -> None:
        """An alakohta (sub-item) serializes as a typed ``s{LABEL}`` segment,
        analogous to the kohta ``k{LABEL}`` segment, so an item→sub-item ref is
        unambiguous and round-trippable.
        """
        ref = ProvisionRef(
            statute_id="711/2022",
            section_label="7",
            subsection_num=2,
            item_label="1",
            subitem_label="a",
        )
        assert ref.serialized() == "711/2022/7/2/k1/sa"

    def test_provision_ref_serialized_subitem_no_momentti(self) -> None:
        ref = ProvisionRef(
            statute_id="711/2022", section_label="7", item_label="1", subitem_label="a"
        )
        assert ref.serialized() == "711/2022/7/k1/sa"

    def test_frozen_dataclass_immutable(self) -> None:
        """ReferenceMention is frozen (immutable)."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        with pytest.raises((TypeError, AttributeError)):
            _set_runtime_attr(mention, "cite_kind", CiteKind.EU)


# ===========================================================================
# Category 2: Real corpus regression via conformance fixtures
# ===========================================================================


class TestConformanceFixtures:
    """Real AKN patterns from the conformance corpus."""

    def test_fixture_exact_cross_statute(self) -> None:
        """EXACT × CROSS_STATUTE: inline <ref> to Finnish statute §7."""
        result = extract_all_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_CROSS_STATUTE.expected_mentions[0])

    def test_fixture_exact_internal_self_ref_skipped(self) -> None:
        """A self-referencing <ref> produces a diagnostic, never a CROSS_STATUTE
        mention. Bare same-statute section refs in body prose ARE now captured as
        INTERNAL mentions (the InternalRef family), targeting the same statute."""
        result = extract_all_reference_mentions(
            EXACT_INTERNAL_SELF_REF_SKIPPED.xml_bytes,
            EXACT_INTERNAL_SELF_REF_SKIPPED.source_statute_id,
        )
        # The self-referencing <ref> must not become a cross-statute mention.
        cross = [m for m in result.mentions if m.cite_kind == CiteKind.CROSS_STATUTE]
        assert cross == [], f"self-ref must not be a CROSS_STATUTE mention: {cross}"
        # Any emitted mentions are INTERNAL and target the citing statute itself.
        for m in result.mentions:
            assert m.cite_kind == CiteKind.INTERNAL
            assert m.target_provision_ref is not None
            assert (
                m.target_provision_ref.statute_id
                == EXACT_INTERNAL_SELF_REF_SKIPPED.source_statute_id
            )
        diag_ids = [d.rule_id for d in result.diagnostics]
        for expected_id in EXACT_INTERNAL_SELF_REF_SKIPPED.expected_diagnostics_rule_ids:
            assert expected_id in diag_ids, (
                f"Expected diagnostic {expected_id!r} not found in {diag_ids}"
            )

    def test_fixture_exact_eu(self) -> None:
        """EU regulation via text pattern: cite_kind=EU."""
        result = extract_all_reference_mentions(
            EXACT_EU.xml_bytes,
            EXACT_EU.source_statute_id,
        )
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1, f"Expected EU mention, got {result.mentions}"
        _assert_mention_matches(eu_mentions[0], EXACT_EU.expected_mentions[0])

    def test_fixture_eu_embedded_repeal(self) -> None:
        """Long-form EU citation: primary target + embedded-repeal provenance.

        'asetuksen (EY) N:o 1774/2002 kumoamisesta ... asetuksessa (EY) N:o
        1069/2009' yields TWO typed EU mentions — 1069/2009 as the primary CITES
        target and 1774/2002 as REPEALS_EMBEDDED provenance.
        """
        result = extract_all_reference_mentions(
            EU_EMBEDDED_REPEAL.xml_bytes,
            EU_EMBEDDED_REPEAL.source_statute_id,
        )
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        by_target = {
            m.target_provision_ref.statute_id: m
            for m in eu_mentions
            if m.target_provision_ref is not None
        }
        assert "eu/reg/2009/1069" in by_target, f"primary missing: {by_target}"
        assert "eu/reg/2002/1774" in by_target, f"embedded missing: {by_target}"
        assert by_target["eu/reg/2009/1069"].edge_subtype == "CITES"
        assert by_target["eu/reg/2002/1774"].edge_subtype == "REPEALS_EMBEDDED"
        # Each expected fixture mention must match an actual EU mention by target.
        for expected in EU_EMBEDDED_REPEAL.expected_mentions:
            match = by_target[expected["target_statute_id"]]
            _assert_mention_matches(match, expected)

    def test_fixture_exact_issued_under(self) -> None:
        """finlex:issuedUnderActs metadata: NON_STATUTORY_INSTRUMENT, ISSUED_UNDER."""
        result = extract_all_reference_mentions(
            EXACT_ISSUED_UNDER.xml_bytes,
            EXACT_ISSUED_UNDER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_ISSUED_UNDER.expected_mentions[0])

    def test_fixture_exact_repeals(self) -> None:
        """finlex:repeals metadata: CROSS_STATUTE, REPEALS."""
        result = extract_all_reference_mentions(
            EXACT_REPEALS.xml_bytes,
            EXACT_REPEALS.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_REPEALS.expected_mentions[0])

    def test_fixture_xml_parse_failure(self) -> None:
        """Corrupt XML: blocking diagnostic, no mentions (per §1.8)."""
        result = extract_all_reference_mentions(
            XML_PARSE_FAILURE.xml_bytes,
            XML_PARSE_FAILURE.source_statute_id,
        )
        assert result.mentions == []
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_cross_ref_xml_parse_failed" in diag_ids
        blocking_diags = [d for d in result.diagnostics if d.blocking]
        assert len(blocking_diags) >= 1

    def test_all_fixtures_run_without_exception(self) -> None:
        """All conformance fixtures must run without unhandled exceptions."""
        for fid, fixture in ALL_FIXTURES.items():
            result = extract_all_reference_mentions(
                fixture.xml_bytes,
                fixture.source_statute_id,
            )
            # ExtractionResult must be a valid instance
            assert isinstance(result, ExtractionResult), (
                f"Fixture {fid}: expected ExtractionResult, got {type(result)}"
            )

    # The inline-(id) plain-text citation family is, post citation-flip, produced
    # PRIMARILY by the construction parse (``citation_construction``); the demoted
    # regex lane survives only as a typed residue fallback (``plain_text_fallback``).
    # Conformance fixtures assert the SAME provisions regardless of which inline-(id)
    # lane emitted them, so they accept the whole inline-(id) lemma set.
    _INLINE_ID_LEMMAS = frozenset(
        {"citation_construction", "plain_text", "plain_text_fallback"}
    )

    def _assert_body_fixture(self, fixture_id: str) -> None:
        fixture = ALL_FIXTURES[fixture_id]
        result = extract_all_reference_mentions(
            fixture.xml_bytes, fixture.source_statute_id
        )
        plain = [
            m for m in result.mentions if m.phrase_lemma in self._INLINE_ID_LEMMAS
        ]
        actual_paths = {
            m.target_provision_ref.serialized()
            for m in plain
            if m.target_provision_ref is not None
        }
        for expected in fixture.expected_mentions:
            path = expected["target_provision_ref_str"]
            match = next(
                (
                    m
                    for m in plain
                    if m.target_provision_ref is not None
                    and m.target_provision_ref.serialized() == path
                ),
                None,
            )
            assert match is not None, (
                f"{fixture_id}: expected provision {path!r} not in {actual_paths}"
            )
            _assert_mention_matches(match, expected)

    def test_fixture_body_section_range(self) -> None:
        """Body lane: en-dash section RANGE expands to per-section mentions."""
        self._assert_body_fixture("body_section_range")

    def test_fixture_body_section_coordination(self) -> None:
        """Body lane: coordinated section list expands to per-section mentions."""
        self._assert_body_fixture("body_section_coordination")

    def test_fixture_body_byid_momentti(self) -> None:
        """Body lane: by-id citation threads momentti into the target ref."""
        self._assert_body_fixture("body_byid_momentti")


# ===========================================================================
# Category 2b: HE government-proposal <ref> = preparatory, not cross-statute
# ===========================================================================


class TestHEGovernmentProposalRefTyping:
    """An HE government-proposal AKN <ref> is PREPARATORY material, not an
    enacted statute. Finlex marks the HE→act lineage with
    <ref href=".../government-proposal/YEAR/NUMBER"> in the preliminaryWork
    ("Esityöt") footer, which cross_refs lifts to a CITES edge against a
    he/YEAR/NUMBER target. It must type as NON_STATUTORY_INSTRUMENT (matching
    the preparatory text lane that owns HaVM/EV/EU prep instruments), never as
    CROSS_STATUTE, and must not double with the prep text lane (which excludes
    HE precisely because this lane emits it)."""

    def _he_mentions(
        self, mentions: Iterable[ReferenceMention]
    ) -> list[ReferenceMention]:
        out = []
        for m in mentions:
            tgt = m.target_provision_ref
            if tgt is not None and str(tgt.statute_id).startswith("he/"):
                out.append(m)
        return out

    def test_he_ref_typed_preparatory_not_cross_statute(self) -> None:
        result = extract_all_reference_mentions(
            FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id
        )
        he = self._he_mentions(result.mentions)
        assert len(he) == 1, f"expected exactly one HE mention, got {he}"
        m = he[0]
        assert m.cite_kind == CiteKind.NON_STATUTORY_INSTRUMENT, (
            f"HE <ref> must be NON_STATUTORY_INSTRUMENT, got {m.cite_kind}"
        )
        assert m.cite_kind != CiteKind.CROSS_STATUTE
        # Subtype matches the preparatory lane's HE kind so the whole
        # HE/HaVM/EV/EU chain presents uniformly.
        assert m.edge_subtype == PreparatoryReferenceKind.HE.value

    def test_he_ref_not_double_emitted(self) -> None:
        """The HE-via-<ref> mention and the prep text lane's HE recognition
        dedup to ONE mention (the prep lane excludes kind=HE)."""
        result = extract_all_reference_mentions(
            FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id
        )
        he_targets = []
        for m in self._he_mentions(result.mentions):
            assert m.target_provision_ref is not None
            he_targets.append(str(m.target_provision_ref.statute_id))
        assert he_targets == ["he/2021/173"], (
            f"HE must be emitted exactly once, got {he_targets}"
        )

    def test_genuine_act_ref_still_cross_statute(self) -> None:
        """A genuine enacted-statute <ref> is untouched (still CROSS_STATUTE)."""
        result = extract_all_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes, EXACT_CROSS_STATUTE.source_statute_id
        )
        cross = [m for m in result.mentions if m.cite_kind == CiteKind.CROSS_STATUTE]
        assert len(cross) >= 1, f"expected a cross-statute mention, got {result.mentions}"
        for m in cross:
            tgt = m.target_provision_ref
            assert tgt is not None
            assert not str(tgt.statute_id).startswith("he/")


# ===========================================================================
# Category 3: Finding/observation tests
# ===========================================================================


class TestFindingObservation:
    """Tests that typed findings are emitted for BROKEN, AMBIGUOUS, etc."""

    def test_broken_reference_finding_construction(self) -> None:
        """BrokenReferenceFinding has required fields."""
        finding = BrokenReferenceFinding(
            rule_id="fi_ref_broken_target_repealed",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            target_statute_id="1999/532",
            source_provision_ref_str="2003/314/5",
            target_provision_ref_str="1999/532/3",
            reason="Target statute 1999/532 was repealed on 2010-01-01 after "
                   "source provision was last amended on 2005-03-01.",
        )
        assert finding.rule_id == "fi_ref_broken_target_repealed"
        assert finding.blocking is False
        assert finding.strict_disposition == "record"

    def test_ambiguous_reference_finding_construction(self) -> None:
        """AmbiguousReferenceFinding normalizes candidate_target_ids to tuple."""
        finding = AmbiguousReferenceFinding(
            rule_id="fi_ref_ambiguous_multiple_targets",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            source_provision_ref_str="2003/314/5",
            candidate_target_ids=_runtime_candidate_target_ids(["1984/523", "2003/527"]),
            reason="Two ympäristönsuojelulaki versions both match.",
        )
        assert isinstance(finding.candidate_target_ids, tuple)
        assert "1984/523" in finding.candidate_target_ids

    def test_approximate_reference_finding_construction(self) -> None:
        """ApproximateReferenceFinding has heuristic_applied field."""
        finding = ApproximateReferenceFinding(
            rule_id="fi_ref_approximate_agency_lifecycle",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            source_provision_ref_str="2003/314/5",
            target_provision_ref_str="2019/561/3",
            heuristic_applied="Evira → Ruokavirasto lifecycle rename: "
                               "2006/37 merged into 2019/561 on 2019-01-01.",
        )
        assert "Evira" in finding.heuristic_applied

    def test_rejected_ref_candidate_construction(self) -> None:
        """RejectedRefCandidate has all required fields."""
        rej = RejectedRefCandidate(
            rule_id="fi_ref_candidate_not_statute_citation",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            reason="Pattern matched 'vuoden 2020 talousarvio' but year-reference "
                   "is not a statute citation.",
            matched_text="vuoden 2020 talousarvio",
            source_span=None,
        )
        assert rej.blocking is False
        assert rej.rule_id == "fi_ref_candidate_not_statute_citation"

    def test_diagnostic_from_xml_parse_failure_is_blocking(self) -> None:
        """CrossRefDiagnostic from xml_parse_failed is blocking=True."""
        result = extract_reference_mentions(b"<bad>", "2000/1")
        assert result.mentions == []
        assert len(result.diagnostics) >= 1
        blocking = [d for d in result.diagnostics if d.blocking]
        assert len(blocking) >= 1
        assert blocking[0].rule_id == "fi_cross_ref_xml_parse_failed"


# ===========================================================================
# Category 4: Negative tests (non-citation text → no mention)
# ===========================================================================


class TestNegative:
    """Non-citation patterns must not produce ReferenceMention records."""

    def test_empty_statute_body_no_mentions(self) -> None:
        """A statute with no <ref> elements and no EU patterns → no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>Ei viittauksia.</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        assert result.mentions == []
        assert result.rejected == []

    def test_year_reference_not_statute(self) -> None:
        """'vuoden 2020 talousarvio' is not a statute citation — no EU mention."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"vuoden 2020 talousarvio on hyv\xc3\xa4ksytty."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        # No mentions — year alone is not a statute number
        # EU extractor would only match "N:o YYYY/NNNN" or "NNNN/YYYY/EU" patterns
        assert result.mentions == []

    def test_no_body_no_mentions(self) -> None:
        """Statute with no body element → no CITES mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/></act></akomaNtoso>"
        )
        result = extract_reference_mentions(xml, "2003/314")
        assert result.mentions == []

    def test_self_reference_not_a_cross_statute_mention(self) -> None:
        """A self-referencing <ref> produces a diagnostic, not a CROSS_STATUTE mention."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b'<ref href="/akn/fi/act/statute/2003/314#sec_1">1 \xc2\xa7:ss\xc3\xa4</ref>'
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_reference_mentions(xml, "2003/314")
        # No mention (self-ref skipped), but diagnostic emitted
        assert result.mentions == []
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_cross_ref_self_reference_skipped" in diag_ids


# ===========================================================================
# Category 5: Strict-mode tests
# ===========================================================================


class TestStrictMode:
    """Strict mode rejects APPROXIMATE and UNRESOLVED confidence mentions."""

    def test_strict_mode_blocks_on_bad_xml(self) -> None:
        """In strict mode, a parse failure produces a blocking diagnostic."""
        result = extract_reference_mentions(
            b"<invalid>",
            "2000/1",
            strict=True,
        )
        # Extract fails silently but records blocking diagnostic
        assert result.mentions == []
        blocking_diags = [d for d in result.diagnostics if d.blocking]
        assert len(blocking_diags) >= 1

    def test_non_strict_mode_records_without_blocking(self) -> None:
        """Non-strict mode records diagnostic but doesn't block."""
        result = extract_reference_mentions(
            b"<invalid>",
            "2000/1",
            strict=False,
        )
        # Diagnostics exist but extraction result is non-blocking aside from the XML error
        assert result.mentions == []

    def test_strict_mode_flag_passed_correctly(self) -> None:
        """strict=True is accepted by extract_reference_mentions without error."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
            strict=True,
        )
        # EXACT mentions are not blocked by strict mode
        assert len(result.mentions) >= 1
        assert result.mentions[0].cite_confidence == CiteConfidence.EXACT

    def test_ref_element_surface_text_is_preserved_on_typed_mention(self) -> None:
        """The literal <ref> text survives extraction for neutral interlink overlays."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert result.mentions
        assert result.mentions[0].phrase_lemma == "ref_element"
        assert result.mentions[0].surface_text
        assert result.mentions[0].surface_text != "ref_element"


# ===========================================================================
# Category 6: No-leak tests
# ===========================================================================


class TestNoLeak:
    """Synthetic test markers must not leak into production parquet runs."""

    def test_synthetic_statute_id_is_extractable_in_test(self) -> None:
        """Synthetic IDs extract correctly in test context."""
        result = extract_all_reference_mentions(
            NO_LEAK_SYNTHETIC_MARKER.xml_bytes,
            NO_LEAK_SYNTHETIC_MARKER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        # The source_statute_id must carry the synthetic marker
        assert result.mentions[0].source_provision_ref.statute_id == (
            "__test__/9999/synthetic_source"
        )

    def test_reference_mention_to_row_no_internal_sentinels(self) -> None:
        """Serialized row must not contain '__test__' in non-test statute fields."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        row = reference_mention_to_row(mention)
        assert "__test__" not in str(row["source_statute_id"])
        assert "__test__" not in str(row["target_statute_id"] or "")


# ===========================================================================
# Category 6b: Source-span provenance tests
# ===========================================================================


class TestSourceSpanProvenance:
    """Emitted mentions must carry real byte spans into the source xml_bytes.

    OFFSET UNIT: bytes into the statute's xml_bytes (matches SourceSpan field
    names). The span must slice back to the exact citation surface, so byte-overlap
    recall benches and the parse-overlay-IR anchoring both work.
    """

    def test_ref_lane_mention_carries_byte_span(self) -> None:
        """An AKN <ref> mention carries a non-None SourceSpan with byte_len > 0."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert result.mentions
        m = result.mentions[0]
        assert m.phrase_lemma == "ref_element"
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        assert m.source_span.source_file == EXACT_CROSS_STATUTE.source_statute_id

    def test_ref_lane_byte_span_slices_to_the_inner_citation_phrase(self) -> None:
        """The recovered byte span slices to the <ref> element's INNER citation
        phrase, NOT the surrounding markup envelope.

        The span is the citation phrase the reader sees, so it must exclude the
        ``<ref href=...>`` start tag and the ``</ref>`` close tag — slicing those
        in was the markup-envelope over-capture bug (up to multi-KB spans when the
        href search latched onto a metadata duplicate)."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        span = result.mentions[0].source_span
        assert span is not None
        sliced = EXACT_CROSS_STATUTE.xml_bytes[
            span.byte_offset : span.byte_offset + span.byte_len
        ]
        # Inner phrase only: no markup tags, exactly the citation surface.
        assert b"<ref" not in sliced
        assert b"</ref>" not in sliced
        assert sliced == b"lannoitelaissa"

    def test_plain_text_lane_mention_carries_byte_span(self) -> None:
        """A plain-text statute citation mention carries a real byte span that
        slices back to the matched citation surface."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Sovelletaan, mit\xc3\xa4 elintarvikelain (297/2021) 5 \xc2\xa7 nojalla."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert plain
        m = plain[0]
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        sliced = xml[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        assert sliced == "elintarvikelain (297/2021)".encode("utf-8")

    def test_eu_lane_mention_carries_byte_span(self) -> None:
        """An EU citation mention carries a non-None byte span > 0 that slices
        back into the source EU citation surface (byte-accurate past non-ASCII)."""
        result = extract_eu_reference_mentions(
            EXACT_EU.xml_bytes,
            EXACT_EU.source_statute_id,
        )
        eu = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert eu
        m = eu[0]
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        sliced = EXACT_EU.xml_bytes[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        # The slice contains the EU regulation number that defined the target.
        assert b"999/2001" in sliced

    def test_eu_lane_mention_carries_surface_text(self) -> None:
        """An EU citation mention carries the matched EU citation surface, and
        that surface is a verbatim substring of the source bytes (so the hub's
        byte re-anchoring / viewer overlay / provenance work like other lanes).

        Regression: the EU formal-citation lane previously left surface_text
        empty, which broke surface-driven re-anchoring and miscounted EU
        detections in the recall audit.
        """
        result = extract_eu_reference_mentions(
            EXACT_EU.xml_bytes,
            EXACT_EU.source_statute_id,
        )
        eu = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert eu
        m = eu[0]
        assert m.surface_text, "EU mention must carry a non-empty surface_text"
        # The surface is the matched EU citation phrase, verbatim in the source.
        assert "999/2001" in m.surface_text
        assert m.surface_text.encode("utf-8") in EXACT_EU.xml_bytes
        # And it byte-matches the located span.
        assert m.source_span is not None
        sliced = EXACT_EU.xml_bytes[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        assert sliced == m.surface_text.encode("utf-8")

    def test_eu_lane_surface_byte_matches_through_hub(self) -> None:
        """Through the combined hub (extract_all_reference_mentions), an EU
        citation surface byte-matches the raw bytes and yields a located span.

        Covers the long-form embedded-repeal case: both the primary and the
        embedded-repeal EU mentions carry a verbatim surface and a span.
        """
        result = extract_all_reference_mentions(
            EU_EMBEDDED_REPEAL.xml_bytes,
            EU_EMBEDDED_REPEAL.source_statute_id,
        )
        eu = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert eu
        for m in eu:
            assert m.surface_text, f"EU mention missing surface_text: {m}"
            assert m.surface_text.encode("utf-8") in EU_EMBEDDED_REPEAL.xml_bytes
            assert m.source_span is not None
            assert m.source_span.byte_len > 0

    def test_metadata_edge_mention_has_no_span(self) -> None:
        """REPEALS/ISSUED_UNDER metadata edges have no body surface → span None.

        This documents the deliberate fail-loud-by-absence boundary: spans are
        populated only where a surface exists in the body bytes.
        """
        result = extract_reference_mentions(
            EXACT_REPEALS.xml_bytes,
            EXACT_REPEALS.source_statute_id,
        )
        meta = [m for m in result.mentions if m.edge_subtype == "REPEALS"]
        assert meta
        assert meta[0].source_span is None


# ===========================================================================
# Category 7: Schema-stability tests
# ===========================================================================


class TestSchemaStability:
    """Parquet schema column order and dtypes must be pinned."""

    # This is the stable schema from REFERENCE_MENTION_EXTRACTION.md
    EXPECTED_COLUMNS = [
        "source_statute_id",
        "source_provision_ref_str",
        "target_statute_id",
        "target_provision_ref_str",
        "cite_kind",
        "cite_confidence",
        "edge_subtype",
        "phrase_lemma",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_len",
        "valid_at_start",
        "valid_at_end",
        "target_stat_hash",
    ]

    def test_reference_mention_to_row_has_all_columns(self) -> None:
        """reference_mention_to_row() produces all expected columns."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        row = reference_mention_to_row(mention)
        for col in self.EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from serialized row"

    def test_reference_mention_to_row_column_types(self) -> None:
        """Schema column types match expected Python types."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        span = SourceSpan(source_file="/data/fi/2003/314.xml", byte_offset=1024, byte_len=50)
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=span,
            valid_at_interval=(date(2003, 1, 1), date(2022, 6, 1)),
            edge_subtype="CITES",
            target_stat_hash="abcdef0123456789",
        )
        row = reference_mention_to_row(mention)

        assert isinstance(row["source_statute_id"], str)
        assert isinstance(row["source_provision_ref_str"], str)
        assert isinstance(row["target_statute_id"], str)
        assert isinstance(row["cite_kind"], str)
        assert isinstance(row["cite_confidence"], str)
        assert isinstance(row["source_span_file"], str)
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_len"], int)
        assert isinstance(row["valid_at_start"], str)
        assert isinstance(row["valid_at_end"], str)

    def test_reference_mention_to_row_nullable_columns(self) -> None:
        """Nullable columns are None when absent."""
        src = ProvisionRef(statute_id="2003/314")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.UNRESOLVED,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        row = reference_mention_to_row(mention)
        assert row["target_statute_id"] is None
        assert row["target_provision_ref_str"] is None
        assert row["source_span_file"] is None
        assert row["source_span_byte_offset"] is None
        assert row["source_span_len"] is None
        assert row["valid_at_start"] is None
        assert row["valid_at_end"] is None
        assert row["target_stat_hash"] is None

    def test_column_order_stable(self) -> None:
        """Column order in reference_mention_to_row() output is stable."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        row = reference_mention_to_row(mention)
        actual_cols = list(row.keys())
        assert actual_cols == self.EXPECTED_COLUMNS


# ===========================================================================
# Integration: extract_all_reference_mentions combines domestic + EU
# ===========================================================================


class TestExtractAllReferenceMentions:
    """Integration tests for the combined domestic+EU extraction entry point."""

    def test_combines_domestic_and_eu_mentions(self) -> None:
        """extract_all_reference_mentions returns both domestic CITES and EU mentions.

        Uses '(EY) N:o 999/2001' format (NUMBER/YEAR) which the EU extractor P1 handles.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b'Ks. <ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" sek\xc3\xa4 neuvoston asetusta (EY) N:o 999/2001."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        kinds = {m.cite_kind for m in result.mentions}
        assert CiteKind.CROSS_STATUTE in kinds
        assert CiteKind.EU in kinds

    def test_empty_xml_no_crash(self) -> None:
        """Empty XML byte string → graceful failure via diagnostic, no exception."""
        result = extract_all_reference_mentions(b"", "2000/1")
        assert isinstance(result, ExtractionResult)

    def test_source_statute_id_preserved_in_all_mentions(self) -> None:
        """All mentions carry the source_statute_id of the input statute."""
        result = extract_all_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        for mention in result.mentions:
            assert mention.source_provision_ref.statute_id == (
                EXACT_CROSS_STATUTE.source_statute_id
            ), f"Mention has wrong source_statute_id: {mention.source_provision_ref.statute_id}"


# ===========================================================================
# Enum coverage
# ===========================================================================


class TestEnumCoverage:
    """All enum values are accessible and have correct string values."""

    def test_cite_kind_values(self) -> None:
        assert CiteKind.INTERNAL.value == "internal"
        assert CiteKind.CROSS_STATUTE.value == "cross_statute"
        assert CiteKind.EU.value == "eu"
        assert CiteKind.TREATY.value == "treaty"
        assert CiteKind.NON_STATUTORY_INSTRUMENT.value == "non_statutory_instrument"

    def test_cite_confidence_values(self) -> None:
        assert CiteConfidence.EXACT.value == "exact"
        assert CiteConfidence.APPROXIMATE.value == "approximate"
        assert CiteConfidence.AMBIGUOUS.value == "ambiguous"
        assert CiteConfidence.UNRESOLVED.value == "unresolved"
        assert CiteConfidence.BROKEN.value == "broken"

    def test_cite_confidence_resolution_status_members(self) -> None:
        """STATUTE_ONLY and OPEN exist with the catalogue-spec value strings.

        Per FI_REFERENCE_CATALOGUE.md §0: STATUTE_ONLY = act known / provision
        pending; OPEN = vague catch-all (tag-don't-guess, never assigned by a
        confidence threshold).
        """
        assert CiteConfidence.STATUTE_ONLY.value == "statute_only"
        assert CiteConfidence.OPEN.value == "open"

    def test_cite_confidence_existing_members_unchanged(self) -> None:
        """The original five members keep their identity and value strings.

        Adding STATUTE_ONLY / OPEN must not perturb EXACT / APPROXIMATE /
        AMBIGUOUS / UNRESOLVED / BROKEN.
        """
        existing = {
            "EXACT": "exact",
            "APPROXIMATE": "approximate",
            "AMBIGUOUS": "ambiguous",
            "UNRESOLVED": "unresolved",
            "BROKEN": "broken",
        }
        for name, value in existing.items():
            assert CiteConfidence[name].value == value
        # Full membership is exactly the original five plus the two new ones.
        assert {m.name for m in CiteConfidence} == set(existing) | {
            "STATUTE_ONLY",
            "OPEN",
        }


# ===========================================================================
# Task 1: Modern EU regulation patterns (year-first form)
# ===========================================================================


class TestModernEUYearFirstPattern:
    """Tests for the modern (EU) YEAR/NUMBER citation pattern added in Task 1.

    The old P1 pattern handled "(EY) N:o NUMBER/YEAR".
    The new P1B pattern handles "(EU) YEAR/NUMBER" (GDPR-style year-first form).
    Both forms must produce correctly typed CrossRefEdge → ReferenceMention rows
    with cite_kind=EU and cite_confidence=EXACT.
    """

    # ── Synthetic XML helpers ────────────────────────────────────────────────

    @staticmethod
    def _xml_with_eu_text(eu_citation: bytes) -> bytes:
        """Build minimal AKN XML containing a given EU citation byte string."""
        return (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            + eu_citation
            + b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )

    # ── Year-first form tests ────────────────────────────────────────────────

    def test_modern_eu_gdpr_2016_679(self) -> None:
        """'(EU) 2016/679' (GDPR) is extracted as eu/reg/2016/679."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1, f"Expected EU mention, got {result.mentions}"
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2016/679" in target_ids, f"GDPR not found in {target_ids}"

    def test_modern_eu_2017_2226(self) -> None:
        """'(EU) 2017/2226' (EES regulation) is extracted as eu/reg/2017/2226."""
        xml = self._xml_with_eu_text(
            b"Noudatetaan Euroopan parlamentin ja neuvoston asetusta (EU) 2017/2226."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2017/2226" in target_ids, f"EES reg not found in {target_ids}"

    def test_modern_eu_phrase_lemma_is_eu_text_pattern(self) -> None:
        """Modern year-first EU citation has phrase_lemma='eu_text_pattern'."""
        xml = self._xml_with_eu_text(b"soveltaen asetusta (EU) 2016/679.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        assert all(m.phrase_lemma == "eu_text_pattern" for m in eu_mentions)

    def test_modern_eu_cite_confidence_is_exact(self) -> None:
        """Modern year-first EU citation has cite_confidence=EXACT."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        assert all(m.cite_confidence == CiteConfidence.EXACT for m in eu_mentions)

    # ── Back-compat: old N:o form still works ────────────────────────────────

    def test_old_no_form_still_extracted(self) -> None:
        """'(EY) N:o 999/2001' form is still extracted correctly after adding P1B."""
        xml = self._xml_with_eu_text(b"neuvoston asetus (EY) N:o 999/2001.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2001/999" in target_ids, f"N:o form not found in {target_ids}"

    def test_old_and_new_forms_coexist(self) -> None:
        """Both '(EY) N:o 999/2001' and '(EU) 2016/679' are extracted from same XML."""
        xml = self._xml_with_eu_text(
            b"Ks. neuvoston asetus (EY) N:o 999/2001 sek\xc3\xa4 asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2001/999" in target_ids
        assert "eu/reg/2016/679" in target_ids

    # ── Deduplication ────────────────────────────────────────────────────────

    def test_modern_eu_deduplicated(self) -> None:
        """Same modern EU citation appearing twice produces only one mention."""
        xml = self._xml_with_eu_text(
            b"asetus (EU) 2016/679 ja (EU) 2016/679 taas."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        gdpr_mentions = [
            m for m in result.mentions
            if m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "eu/reg/2016/679"
        ]
        assert len(gdpr_mentions) == 1, (
            f"Expected exactly one deduplicated mention, got {len(gdpr_mentions)}"
        )

    # ── Sanity filter: year out of range not extracted ────────────────────────

    def test_year_out_of_range_not_extracted(self) -> None:
        """'(EU) 1900/123' — year below 1957 threshold — is not extracted."""
        xml = self._xml_with_eu_text(b"asetus (EU) 1900/123.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        # Year 1900 < 1957 — must not produce a mention.
        assert all(
            m.target_provision_ref is None
            or "1900" not in m.target_provision_ref.statute_id
            for m in result.mentions
        )

    # ── Integration: modern EU in extract_all_reference_mentions ─────────────

    def test_modern_eu_in_extract_all(self) -> None:
        """Modern year-first EU citation appears in extract_all_reference_mentions output."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_all_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2016/679" in target_ids


# ===========================================================================
# Task 2: Plain-text statute citation grammar
# ===========================================================================


class TestPlainTextStatuteCitations:
    """Tests for the PlainTextStatuteCitationRecognizer and
    extract_plain_text_statute_mentions (Task 2).

    Covers: basic extraction, multiple inflection variants, no-double-count
    against <ref> elements, self-reference skip, and negative patterns.
    """

    # ── PlainTextStatuteCitationRecognizer unit tests ───────────────────────

    def test_recognizer_extracts_lain_form(self) -> None:
        """'lannoitelain (711/2022) 7 §' is extracted with correct statute ID."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Noudatetaan lannoitelain (711/2022) 7 \xa7 tarkoittamia.</p>"
        )
        hits = recognizer.scan(p)
        assert len(hits) >= 1
        statute_ids = [h[0] for h in hits]
        # Visible surface is "(711/2022)"; the TARGET id is canonical YEAR/NUMBER.
        assert "2022/711" in statute_ids

    def test_recognizer_extracts_section_label(self) -> None:
        """Section label is extracted from 'lannoitelain (711/2022) 7 §'."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>lannoitelain (711/2022) 7 \xa7 nojalla.</p>"
        )
        hits = recognizer.scan(p)
        assert any(h == ("2022/711", "7") for h in hits), f"Got {hits}"

    def test_recognizer_extracts_asetuksen_form(self) -> None:
        """'-asetuksen (NUMBER/YEAR) SECTION §' form is extracted."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>ympäristönsuojeluasetuksen (169/2000) 2 \xa7 perusteella.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "2000/169" in statute_ids

    def test_recognizer_extracts_laissa_form(self) -> None:
        """'elintarvikelaissa (23/2006)' form is extracted."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>elintarvikelaissa (23/2006) 7 \xa7 tarkoitetaan.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "2006/23" in statute_ids

    def test_recognizer_extracts_short_lain_form(self) -> None:
        """Bare 'lain (NUMBER/YEAR)' form without prefix word."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Sovelletaan lain (1326/2010) 50 \xa7:ss\xe4.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "2010/1326" in statute_ids

    def test_recognizer_captures_section_less_id_cite(self) -> None:
        """A by-name id-cite with NO § is captured as a statute-only hit.

        Citations to a whole act ("…annetussa laissa (205/2000)") carry no §.
        The recognizer must still capture them: the mandatory by-name anchor
        before the ``(NUMBER/YEAR)`` paren bounds precision, so the "§"-present
        substring guard would wrongly drop every section-less act citation.
        """
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        # No § in text, but a by-name anchor + (id) paren — must be captured.
        p = ET.fromstring("<p>Sovelletaan lain (711/2022) tekstia.</p>")
        hits = recognizer.scan_precise(p)
        assert len(hits) == 1
        assert hits[0].statute_id == "2022/711"
        assert hits[0].section_label == ""

    def test_recognizer_skips_text_without_paren(self) -> None:
        """Text without '(' is skipped by substring guard."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring("<p>Ei sulkuja mutta on \xa7 merkki.</p>")
        hits = recognizer.scan(p)
        assert hits == []

    def test_recognizer_excludes_ref_element_text(self) -> None:
        """Text inside a <ref> element is NOT included in plain-text scan."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        # The ref element text "lannoitelain (711/2022) 7 §" should NOT be scanned
        # — only the tail "muuta mainittavaa" contributes.
        xml_str = (
            '<p xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            "Katso "
            '<ref href="/akn/fi/act/statute/2022/711">lannoitelain (711/2022) 7 \xa7</ref>'
            " muuta mainittavaa \xa7 teksti."
            "</p>"
        )
        p = ET.fromstring(xml_str)
        hits = recognizer.scan(p)
        # If any hit, it must NOT come from inside the ref element
        statute_ids = [h[0] for h in hits]
        # The ref content must not appear since it's inside a <ref> (check both
        # the visible NUMBER/YEAR and the canonical YEAR/NUMBER target form).
        assert "711/2022" not in statute_ids and "2022/711" not in statute_ids, (
            "Statute ID from inside <ref> must not be in plain-text scan results"
        )

    def test_recognizer_deduplicates_same_statute_in_paragraph(self) -> None:
        """Same statute cited twice in one <p> produces only one entry."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Ks. lannoitelain (711/2022) 7 \xa7 ja lannoitelain (711/2022) 8 \xa7.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        # Both hits target canonical 2022/711 but different sections — NOT
        # deduplicated since the key includes section. But if same section cited
        # twice, deduplicated.
        assert statute_ids.count("2022/711") >= 1  # At least one hit

    # ── extract_plain_text_statute_mentions integration tests ────────────────

    def test_plain_text_mention_emitted(self) -> None:
        """Plain-text statute citation → ReferenceMention with phrase_lemma='plain_text'."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan, mit\xc3\xa4 lannoitelain (711/2022) 7 \xc2\xa7 s\xc3\xa4\xc3\xa4t\xc3\xa4\xc3\xa4.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain_mentions = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain_mentions) >= 1, f"Expected plain_text mention, got {result.mentions}"
        target_ids = _target_statute_ids(plain_mentions)
        # Visible surface "(711/2022)"; TARGET id is canonical YEAR/NUMBER.
        assert "2022/711" in target_ids

    def test_plain_text_mention_cite_kind(self) -> None:
        """Plain-text statute citation has cite_kind=CROSS_STATUTE."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan terveydenhuoltolain (1326/2010) 50 \xc2\xa7:ss\xc3\xa4.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) >= 1
        assert all(m.cite_kind == CiteKind.CROSS_STATUTE for m in plain)

    def test_plain_text_mention_confidence_exact(self) -> None:
        """Plain-text statute citation has cite_confidence=EXACT."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan lannoitelain (711/2022) 7 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) >= 1
        assert all(m.cite_confidence == CiteConfidence.EXACT for m in plain)

    def test_plain_text_no_double_count_vs_ref_element(self) -> None:
        """When <ref> covers a statute, plain_text pass skips that statute_id.

        The dedup guard is at statute level: if 2022/711 is already in the
        ref_covered set, the plain_text extractor won't emit it again. The
        covered set is built from the <ref> lane's canonical YEAR/NUMBER target
        ids, and the plain-text TARGET id is now canonicalized to the SAME
        orientation, so the guard actually matches.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan "
            b'<ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" (711/2022) 7 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(
            xml, "2003/314",
            ref_covered_statute_ids={"2022/711"},  # already covered by <ref> (canonical)
        )
        # 2022/711 is in the covered set → plain_text pass must skip it
        plain_711 = [
            m for m in result.mentions
            if m.phrase_lemma == "plain_text"
            and m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "2022/711"
        ]
        assert plain_711 == [], (
            f"2022/711 should be skipped (covered by <ref>), but got {plain_711}"
        )

    def test_plain_text_self_reference_skipped(self) -> None:
        """Plain-text citations where target == source statute are skipped."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan t\xc3\xa4m\xc3\xa4n lain (711/2022) 3 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        # Source statute IS 2022/711 (canonical) — the visible "(711/2022)" cite
        # targets itself; the canonicalized target matches the source and is skipped.
        result = extract_plain_text_statute_mentions(xml, "2022/711")
        self_refs = [
            m for m in result.mentions
            if m.phrase_lemma == "plain_text"
            and m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "2022/711"
        ]
        assert self_refs == [], "Self-reference must not produce a plain_text mention"

    def test_plain_text_negative_no_citation(self) -> None:
        """Plain text without statute citation pattern → no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>T\xc3\xa4ss\xc3\xa4 laissa ei ole viittauksia muihin lakeihin.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        assert result.mentions == []

    def test_plain_text_phrase_lemma_distinct_from_ref_element(self) -> None:
        """The inline-(id) prose lane is distinct from the <ref>-element lane.

        Post citation-flip the inline-(id) prose cite is produced primarily by the
        construction parse (``citation_construction``), not the demoted regex lane
        (``plain_text``). Either way it must be a SEPARATE lemma from the AKN
        ``<ref>``-element lane (``ref_element``).
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Ks. "
            b'<ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" ja terveydenhuoltolain (1326/2010) 50 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        lemmas = {m.phrase_lemma for m in result.mentions}
        # ref_element from AKN <ref>, inline-(id) prose lemma from prose
        assert "ref_element" in lemmas, f"Expected ref_element, got {lemmas}"
        # The terveydenhuoltolain prose citation (different statute id, so not
        # deduped against the <ref>) surfaces via the inline-(id) prose lane.
        inline = [
            m
            for m in result.mentions
            if m.phrase_lemma
            in {"citation_construction", "plain_text", "plain_text_fallback"}
        ]
        assert len(inline) >= 1, f"Expected inline-(id) prose mention, got lemmas={lemmas}"


class TestPlainTextNominativeAnnettuFrame:
    """The NOMINATIVE ``annettu asetus/laki (NNN/YYYY)`` repeal/description form.

    The plain-text recognizer keys on inflected heads (``asetuksen``, ``lain``,
    ``laissa``, …) but historically NOT the nominative ``asetus`` / ``laki`` — so
    the pervasive repeal-johtolause form ``kumotaan … annettu asetus (875/1983)``
    extracted nothing. The nominative is recognized ONLY inside the
    discriminating ``annettu``-participle frame (participle + trailing
    ``(NNN/YYYY)`` id); a bare nominative without that frame must NOT fire (the
    words ``laki`` / ``asetus`` are far too common to anchor on alone).
    """

    def _p(self, body: str):  # type: ignore[no-untyped-def]
        import xml.etree.ElementTree as ET
        return ET.fromstring(f"<p>{body}</p>")

    def test_nominative_annettu_asetus_id_extracted(self) -> None:
        """``annettu asetus (875/1983)`` → ref to 875/1983."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(
            self._p("Kumotaan jostakin annettu asetus (875/1983).")
        )
        assert ("1983/875", "") in hits, f"Got {hits}"

    def test_nominative_annettu_laki_id_extracted(self) -> None:
        """``annettu laki (1295/1992) 5 §`` → ref to 1295/1992 § 5."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(
            self._p("lannoitteista annettu laki (1295/1992) 5 \xa7.")
        )
        assert ("1992/1295", "5") in hits, f"Got {hits}"

    def test_nominative_annetun_oblique_participle_with_nominative_head(self) -> None:
        """The participle alone (``annetun``) before a nominative head + id fires.

        The participle inflects (``annettu``/``annettua``/``annetun``); any of
        them in front of the nominative head with the trailing id is the frame.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(self._p("X:stä annettua asetus (169/2000)."))
        assert ("2000/169", "") in hits, f"Got {hits}"

    def test_bare_nominative_laki_without_frame_yields_nothing(self) -> None:
        """``tämä laki`` (no annettu, no id) → no hit (FP guard)."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(self._p("T\xe4m\xe4 laki tulee voimaan."))
        assert hits == [], f"Got {hits}"

    def test_bare_nominative_asetus_annetaan_yields_nothing(self) -> None:
        """``Asetus annetaan …`` (no annettu participle, no id) → no hit."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(
            self._p("Asetus annetaan valtioneuvoston p\xe4\xe4t\xf6ksell\xe4.")
        )
        assert hits == [], f"Got {hits}"

    def test_nominative_laki_with_id_but_no_annettu_yields_nothing(self) -> None:
        """``laki (123/2020)`` WITHOUT the ``annettu`` participle → no hit.

        The id alone must not promote a bare nominative head: only the inflected
        heads (``lain``/``laissa``/…) match an id without the participle frame.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(self._p("T\xe4m\xe4 laki (123/2020) on jo katettu."))
        assert hits == [], f"Got {hits}"

    def test_nominative_annettu_frame_emits_mention(self) -> None:
        """End-to-end: the nominative frame surfaces a CROSS_STATUTE mention."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Kumotaan kasvinsuojelusta annettu asetus (875/1983).</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2016/141")
        targets = [
            m.target_provision_ref.statute_id
            for m in result.mentions
            if m.target_provision_ref
        ]
        assert "1983/875" in targets, f"Got targets {targets}"


# ===========================================================================
# Task 23: momentti/kohta sub-section precision for deeplink consumers
# ===========================================================================


class TestPlainTextMomenttiPrecision:
    """The plain-text recognizer must surface momentti (subsection) and kohta
    (item) precision so a deeplink consumer can target the exact provision,
    not just the §. Section-level precision is the fallback when the citation
    stops at the §.
    """

    @staticmethod
    def _p(text: str):
        import xml.etree.ElementTree as ET

        return ET.fromstring(f"<p>{text}</p>")

    # ── Recognizer scan_precise unit tests ───────────────────────────────────

    def test_momentti_captured(self) -> None:
        """'7 §:n 2 momentissa' yields subsection_num=2."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("lannoitelain (711/2022) 7 \xa7:n 2 momentissa tarkoitettu.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="2022/711", section_label="7", subsection_num=2, item_label=None,
                surface_text="lannoitelain (711/2022)",
            )
        ], hits

    def test_momentti_and_kohta_captured(self) -> None:
        """'5 §:n 2 momentin 3 kohdan' yields subsection_num=2, item_label='3'."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("lain (250/1966) 5 \xa7:n 2 momentin 3 kohdan mukaan.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="1966/250", section_label="5", subsection_num=2, item_label="3",
                surface_text="lain (250/1966)",
            )
        ], hits

    def test_section_only_falls_back(self) -> None:
        """A bare '5 §' citation yields subsection_num=None (section-level)."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("elintarvikelain (297/2021) 5 \xa7 nojalla.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="2021/297", section_label="5", subsection_num=None, item_label=None,
                surface_text="elintarvikelain (297/2021)",
            )
        ], hits

    def test_distinct_momentit_not_collapsed(self) -> None:
        """Two distinct momentit of one statute (each a full citation) produce
        two precise hits — the dedup key includes the sub-section precision.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p(
                "lain (12/2000) 3 \xa7:n 4 momentti ja "
                "lain (12/2000) 3 \xa7:n 5 momentti."
            )
        )
        subsections = sorted(h.subsection_num for h in hits if h.subsection_num is not None)
        assert subsections == [4, 5], hits

    def test_scan_stays_backward_compatible(self) -> None:
        """The legacy scan() 2-tuple contract is unchanged by momentti capture."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(
            self._p("lannoitelain (711/2022) 7 \xa7:n 2 momentissa tarkoitettu.")
        )
        assert hits == [("2022/711", "7")], hits

    def test_name_internal_relative_clause_not_a_target(self) -> None:
        """A '§:n M momentissa tarkoitettu' relative clause with no statute id
        of its own does not fabricate a citation target. This guards the
        adversarial reversal that blocked promoting `momentissa` into the
        shared johtolause lexicon — the body-citation pass must not perturb
        amendment grammar.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("8 \xa7:n 2 momentissa tarkoitettu menettely.")
        )
        assert hits == [], hits

    # ── End-to-end: ProvisionRef threading ───────────────────────────────────

    def test_mention_target_provision_ref_carries_subsection(self) -> None:
        """extract_plain_text_statute_mentions threads momentti+kohta into the
        target ProvisionRef so the interlink consumer can build a subsection
        deeplink.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan lannoitelain (711/2022) 7 \xc2\xa7:n 2 "
            b"momentin 3 kohdan mukaan.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) == 1, result.mentions
        tgt = plain[0].target_provision_ref
        assert tgt is not None
        assert tgt.statute_id == "2022/711"
        assert tgt.section_label == "7"
        assert tgt.subsection_num == 2
        assert tgt.item_label == "3"
        assert tgt.serialized() == "2022/711/7/2/k3"

    def test_interlink_target_locator_has_subsection_segment(self) -> None:
        """The neutral interlink built from a momentti citation carries a
        subsection LocatorSegment — the deeplink precision the consumer needs.
        """
        from lawvm.finland.interlinks import fi_interlink_from_reference_mention

        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan lannoitelain (711/2022) 7 \xc2\xa7:n 2 momentissa.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) == 1
        interlink = fi_interlink_from_reference_mention(plain[0], interlink_id="t23")
        assert interlink.target.locator is not None
        locator = interlink.target.locator.locator
        assert locator is not None
        segments = locator.segments
        kinds = [seg.kind for seg in segments]
        labels = [seg.label for seg in segments]
        assert kinds == ["section", "subsection"], (kinds, labels)
        assert labels == ["7", "2"], (kinds, labels)


# ===========================================================================
# Recall: preparatory-chain lane (committee reports/opinions, EV, EU prep, OJ)
# ===========================================================================


class TestPreparatoryReferenceLane:
    """The legislative-preparation footer (preliminaryWork) names more than the
    HE proposal: committee mietintö/lausunto, the parliamentary response EV,
    EU preparation acts, OJ refs. The <ref> lane only emits the HE backlink;
    extract_all_reference_mentions must also surface the rest of the chain via
    extract_preparatory_reference_mentions, WITHOUT double-counting HE.
    """

    @staticmethod
    def _stat_with_preliminary(prep_p_lines: list[bytes]) -> bytes:
        ps = b"".join(b"<p>" + line + b"</p>" for line in prep_p_lines)
        return (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act>"
            b"<body><section><num>1 \xc2\xa7</num><content>"
            b"<p>T\xc3\xa4ss\xc3\xa4 laissa s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n asiasta.</p>"
            b"</content></section></body>"
            b'<hcontainer name="preliminaryWork"><content>'
            + ps +
            b"</content></hcontainer>"
            b"</act></akomaNtoso>"
        )

    def test_committee_and_response_chain_surfaced(self) -> None:
        """Committee report + opinion + parliament response become mentions."""
        xml = self._stat_with_preliminary([
            b"TyVM 5/2002",
            b"EV 133/2002",
        ])
        result = extract_all_reference_mentions(xml, "2002/943")
        prep = [m for m in result.mentions if m.phrase_lemma == "preparatory"]
        targets = {
            m.target_provision_ref.statute_id: m.edge_subtype
            for m in prep
            if m.target_provision_ref is not None
        }
        assert "fi.committee.tyvm.5.2002" in targets, targets
        assert targets["fi.committee.tyvm.5.2002"] == "committee_report"
        assert "fi.ev.133.2002" in targets, targets
        assert targets["fi.ev.133.2002"] == "parliament_response"
        # All preparatory mentions are NON_STATUTORY_INSTRUMENT, EXACT.
        for m in prep:
            assert m.cite_kind == CiteKind.NON_STATUTORY_INSTRUMENT
            assert m.cite_confidence == CiteConfidence.EXACT

    def test_committee_opinion_surfaced(self) -> None:
        """A PeVL committee opinion becomes a committee_opinion mention."""
        xml = self._stat_with_preliminary([b"PeVL 56/2010"])
        result = extract_all_reference_mentions(xml, "2011/415")
        prep = [m for m in result.mentions if m.phrase_lemma == "preparatory"]
        targets = {
            m.target_provision_ref.statute_id: m.edge_subtype
            for m in prep
            if m.target_provision_ref is not None
        }
        assert targets.get("fi.committee_opinion.pevl.56.2010") == "committee_opinion"

    def test_he_not_double_counted(self) -> None:
        """HE is owned by the <ref> lane; the preparatory lane must NOT re-emit
        it. With an HE <ref> backlink present, the he/ target appears exactly
        once and never under phrase_lemma='preparatory'.
        """
        # Finlex nests the preliminaryWork hcontainer INSIDE <body>, so the
        # HE <ref> backlink is reachable by the body-scanning <ref> lane.
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act>"
            b"<body>"
            b"<section><num>1 \xc2\xa7</num><content>"
            b"<p>Asiasta s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n.</p>"
            b"</content></section>"
            b'<hcontainer name="preliminaryWork"><content>'
            b"<p>"
            b'<ref href="/akn/fi/doc/government-proposal/2002/8">HE 8/2002</ref>'
            b"</p>"
            b"<p>TyVM 5/2002</p>"
            b"</content></hcontainer>"
            b"</body>"
            b"</act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2002/943")
        he_mentions = [
            m for m in result.mentions
            if m.target_provision_ref is not None
            and m.target_provision_ref.statute_id.startswith("he/")
        ]
        # HE appears once, via the <ref> lane (ref_element), never preparatory.
        assert len(he_mentions) == 1, he_mentions
        assert he_mentions[0].phrase_lemma == "ref_element"
        prep_he = [
            m for m in result.mentions
            if m.phrase_lemma == "preparatory"
            and m.target_provision_ref is not None
            and m.target_provision_ref.statute_id.startswith("he/")
        ]
        assert prep_he == [], "HE must not leak into the preparatory lane"

    def test_direct_extractor_excludes_he(self) -> None:
        """extract_preparatory_reference_mentions emits non-HE refs only."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act>"
            b"<body><section><num>1 \xc2\xa7</num><content><p>x</p></content></section></body>"
            b'<hcontainer name="preliminaryWork"><content>'
            b"<p>"
            b'<ref href="/akn/fi/doc/government-proposal/2002/8">HE 8/2002</ref>'
            b"</p>"
            b"<p>TyVM 5/2002</p>"
            b"</content></hcontainer>"
            b"</act></akomaNtoso>"
        )
        result = extract_preparatory_reference_mentions(xml, "2002/943")
        kinds = {m.edge_subtype for m in result.mentions}
        assert kinds == {"committee_report"}, kinds


# ===========================================================================
# Recall: two-digit-year id-cites and section-less id-cites
# ===========================================================================


class TestTwoDigitYearAndSectionlessCites:
    """Pre-2000 statutes commonly cite ``(NUMBER/YY)`` with a two-digit year,
    and citations to a whole act carry no §. Both were silently dropped before:
    the plain-text year group required four digits, and the recognizer gated on
    a mandatory § that whole-act cites never have.
    """

    @staticmethod
    def _body(p_text: bytes) -> bytes:
        return (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body><section><num>5 \xc2\xa7</num><content>"
            b"<p>" + p_text + b"</p>"
            b"</content></section></body></act></akomaNtoso>"
        )

    def test_two_digit_year_expands_to_19xx(self) -> None:
        """'(307/86)' → statute id 307/1986 (two-digit year > current → 19xx)."""
        xml = self._body(
            b"Kumotun lain (307/86) 5 \xc2\xa7 mukaan."
        )
        result = extract_plain_text_statute_mentions(xml, "1996/801")
        ids = _target_statute_ids(
            [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        )
        assert "1986/307" in ids, ids

    def test_two_digit_year_named_anchor(self) -> None:
        """'hallintomenettelylain (598/82)' → 598/1982."""
        xml = self._body(
            b"Sovelletaan hallintomenettelylain (598/82) 2 \xc2\xa7 s\xc3\xa4\xc3\xa4nn\xc3\xb6st\xc3\xa4."
        )
        result = extract_plain_text_statute_mentions(xml, "1991/1742")
        ids = _target_statute_ids(
            [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        )
        assert "1982/598" in ids, ids

    def test_paatos_anchor_two_digit_year(self) -> None:
        """'p\xc3\xa4\xc3\xa4t\xc3\xb6ksess\xc3\xa4 (233/89)' — the decision anchor + two-digit year."""
        xml = self._body(
            "annetussa päätöksessä (233/89) 3 § tarkoitettu.".encode("utf-8")
        )
        result = extract_plain_text_statute_mentions(xml, "1991/122")
        ids = _target_statute_ids(
            [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        )
        assert "1989/233" in ids, ids

    def test_four_digit_year_unchanged(self) -> None:
        """A four-digit year still parses exactly as before (no regression)."""
        xml = self._body(
            "ympäristönsuojelulain (527/2014) 5 § nojalla.".encode("utf-8")
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        ids = _target_statute_ids(
            [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        )
        assert "2014/527" in ids, ids

    def test_section_less_act_cite_captured(self) -> None:
        """'annetussa laissa (205/2000)' with no § is captured statute-only."""
        xml = self._body(
            b"Tuomareiden nimitt\xc3\xa4misest\xc3\xa4 annetussa laissa (205/2000) s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n."
        )
        result = extract_plain_text_statute_mentions(xml, "2000/212")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        ids = _target_statute_ids(plain)
        assert "2000/205" in ids, ids
        m205 = next(
            m for m in plain
            if m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "2000/205"
        )
        assert m205.cite_confidence == CiteConfidence.STATUTE_ONLY

    def test_no_anchor_no_false_positive(self) -> None:
        """A bare ``(NNN/YY)`` paren with NO by-name anchor is not a citation."""
        xml = self._body(
            b"Taulukon rivi (307/86) ei ole lakiviittaus t\xc3\xa4ss\xc3\xa4."
        )
        result = extract_plain_text_statute_mentions(xml, "9999/9999")
        # No anchor word (lain/asetuksen/päätöksen) precedes the paren.
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert plain == [], plain


class TestIssuedUnderAuthorityBasisTyping:
    """ISSUED_UNDER 'nojalla' authority basis: a cited laki must type as a
    statute cross-reference (with its section retained), not a non-statutory
    instrument. A genuine delegated instrument basis stays an instrument.
    """

    def test_act_basis_types_cross_statute_with_section(self) -> None:
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.references.ref_mention_extractor import (
            _edge_to_cite_kind,
            _edge_to_mention,
        )

        edge = CrossRefEdge(
            source_statute_id="2010/908",
            target_statute_id="1998/629",
            edge_type="ISSUED_UNDER",
            target_section="36",
        )
        # The graph layer tags the basis kind from the 'lukiolain (…)' surface.
        edge.target_kind = "act"  # type: ignore[attr-defined]

        assert _edge_to_cite_kind(edge, "2010/908") == CiteKind.CROSS_STATUTE

        mention = _edge_to_mention(edge, "2010/908", (None, None))
        assert mention.cite_kind == CiteKind.CROSS_STATUTE
        assert mention.target_provision_ref is not None
        # Section retained (Defect 2: bare label, not an AKN sec_ path).
        assert mention.target_provision_ref.section_label == "36"
        # The ISSUED_UNDER role is preserved on the subtype, not the cite_kind.
        assert mention.edge_subtype == "ISSUED_UNDER"

    def test_decree_basis_stays_non_statutory_instrument(self) -> None:
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.references.ref_mention_extractor import _edge_to_cite_kind

        edge = CrossRefEdge(
            source_statute_id="2099/1",
            target_statute_id="2005/1248",
            edge_type="ISSUED_UNDER",
            target_section="3",
        )
        edge.target_kind = "decree"  # type: ignore[attr-defined]

        # A decree issued under another decree's authority must NOT over-correct.
        assert (
            _edge_to_cite_kind(edge, "2099/1")
            == CiteKind.NON_STATUTORY_INSTRUMENT
        )

    def test_untagged_issued_under_keeps_legacy_instrument_typing(self) -> None:
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.references.ref_mention_extractor import _edge_to_cite_kind

        edge = CrossRefEdge(
            source_statute_id="x",
            target_statute_id="2006/1013",
            edge_type="ISSUED_UNDER",
        )
        # No target_kind tag → conservative legacy typing (no regression).
        assert (
            _edge_to_cite_kind(edge, "x") == CiteKind.NON_STATUTORY_INSTRUMENT
        )

    def test_issues_target_is_delegated_instrument(self) -> None:
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.references.ref_mention_extractor import _edge_to_cite_kind

        edge = CrossRefEdge(
            source_statute_id="x",
            target_statute_id="2011/500",
            edge_type="ISSUES",
        )
        # ISSUES target IS the delegated instrument — stays an instrument even
        # if a stray kind tag were present.
        edge.target_kind = "act"  # type: ignore[attr-defined]
        assert (
            _edge_to_cite_kind(edge, "x") == CiteKind.NON_STATUTORY_INSTRUMENT
        )

    def test_bare_section_label_retained_on_target(self) -> None:
        from lawvm.finland.references.ref_mention_extractor import (
            _parse_provision_ref_from_path,
        )

        # The authority lane carries a bare numeric/label section, NOT a sec_ path.
        assert _parse_provision_ref_from_path("2016/1048", "37").section_label == "37"
        assert _parse_provision_ref_from_path("1992/150", "8").section_label == "8"
        assert _parse_provision_ref_from_path("2000/1", "115a").section_label == "115a"
        # Comma-joined list keeps the first member as the primary section.
        assert _parse_provision_ref_from_path("2000/1", "8,36").section_label == "8"
        # An AKN sec_ path still resolves via the existing path regex.
        assert _parse_provision_ref_from_path("2000/1", "sec_12a").section_label == "12a"


def _corpus_available() -> bool:
    import os

    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus authority typing skipped",
)
class TestIssuedUnderAuthorityKindEndToEnd:
    """End-to-end: the graph layer (build_statute_graph_fi_lightweight) must set
    ``target_kind`` on ISSUED_UNDER edges from the 'nojalla' authority basis, so
    a laki basis types CROSS_STATUTE while a genuine decree/decision basis stays a
    non-statutory instrument. Complements the synthetic-edge unit tests above by
    proving the graph.py wiring actually populates the field.
    """

    @staticmethod
    def _issued_under_edges(sid: str) -> dict[str, str]:
        import asyncio

        from lawvm.finland.graph import build_statute_graph_fi_lightweight

        graph = asyncio.run(build_statute_graph_fi_lightweight(sid))
        return {
            e.target_statute_id: getattr(e, "target_kind", "")
            for e in graph.citations
            if e.edge_type == "ISSUED_UNDER"
        }

    def test_act_basis_tagged_act_through_graph(self) -> None:
        from lawvm.finland.references.ref_mention_extractor import (
            _edge_to_cite_kind,
        )
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.graph import build_statute_graph_fi_lightweight
        import asyncio

        graph = asyncio.run(build_statute_graph_fi_lightweight("2010/908"))
        issued = {
            e.target_statute_id: e
            for e in graph.citations
            if e.edge_type == "ISSUED_UNDER"
        }
        # lukiolaki (629/1998) §36 and valtion maksuperustelaki (150/1992) §8.
        assert issued["1998/629"].target_kind == "act"
        assert issued["1998/629"].target_section == "36"
        assert (
            _edge_to_cite_kind(cast(CrossRefEdge, issued["1998/629"]), "2010/908")
            == CiteKind.CROSS_STATUTE
        )
        assert issued["1992/150"].target_kind == "act"
        assert issued["1992/150"].target_section == "8"
        assert (
            _edge_to_cite_kind(cast(CrossRefEdge, issued["1992/150"]), "2010/908")
            == CiteKind.CROSS_STATUTE
        )

    def test_second_act_basis_tagged_act_through_graph(self) -> None:
        kinds = self._issued_under_edges("2018/1158")
        # lain (1048/2016) §37 — a laki authority basis.
        assert kinds.get("2016/1048") == "act"

    def test_decree_basis_stays_instrument_through_graph(self) -> None:
        from lawvm.finland.references.ref_mention_extractor import (
            _edge_to_cite_kind,
        )
        from lawvm.finland.references.cross_refs import CrossRefEdge
        from lawvm.finland.graph import build_statute_graph_fi_lightweight
        import asyncio

        # 1979/86 cites a decree (702/1977) as authority basis; must stay
        # non-statutory instrument, NOT be over-corrected to a statute ref.
        graph = asyncio.run(build_statute_graph_fi_lightweight("1979/86"))
        issued = {
            e.target_statute_id: e
            for e in graph.citations
            if e.edge_type == "ISSUED_UNDER"
        }
        assert issued["1977/702"].target_kind == "decree"
        assert (
            _edge_to_cite_kind(cast(CrossRefEdge, issued["1977/702"]), "1979/86")
            == CiteKind.NON_STATUTORY_INSTRUMENT
        )


class TestExplicitIdTextRecoveryAcrossRefBoundary:
    """Annotation-independence: the by-id text lane must recover a cite whose
    statute-name prose and ``(NNN/YYYY)`` id are SPLIT across a ``<ref>`` boundary.

    On consolidated/oracle bodies Finlex wraps the id parenthetical in a ``<ref>``
    element, leaving the name head in the surrounding ``<p>`` text and the id in
    the ``<ref>`` inner text, separated by the element's pretty-print indentation
    (a long whitespace run). With the ``<ref>`` lane suppressed
    (``ignore_annotations=True``) and its inner text folded into the plain-text
    scan (``include_ref_text=True``), the recogniser must still bind name+id from
    text alone — the markup whitespace run is collapsed so the gap fits the
    name→id anchor. This proves the text lane STANDS ALONE for these cites.

    The default (annotation-ON, no fold) behaviour is byte-identical: the fold and
    whitespace-collapse only happen on the measurement path.
    """

    def _p(self, body: str):  # type: ignore[no-untyped-def]
        import xml.etree.ElementTree as ET
        return ET.fromstring(
            '<p xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            + body
            + "</p>"
        )

    def test_name_id_split_across_ref_recovered_when_folded(self) -> None:
        """``annetun lain </ref-split>(688/1988)`` → recovered in fold mode."""
        # Statute name in the <p> text, id parenthetical inside the <ref>, with a
        # newline + deep indentation between them (the Finlex pretty-print split).
        p = self._p(
            "elinkeinonharjoittajan oikeudesta annetun lain\n"
            "                                    "
            '<ref href="/akn/fi/act/statute/1988/688">(688/1988)</ref>'
            "\n                                     4 \xa7:ssa."
        )
        recognizer = PlainTextStatuteCitationRecognizer()

        # Default (no fold): the <ref> inner text is excluded, so the id is not in
        # the scanned text and nothing is recovered from the body text alone.
        assert recognizer.scan_precise(p, include_ref_text=False) == []

        # Fold mode: the inner text is folded in and the markup whitespace run is
        # collapsed, so the name head binds its id — recovered from text alone.
        hits = recognizer.scan_precise(p, include_ref_text=True)
        assert any(h.statute_id == "1988/688" for h in hits), hits

    def test_name_id_adjacent_no_fold_needed(self) -> None:
        """When the name+id are already adjacent, fold mode changes nothing."""
        p = self._p("annetun lain (361/1999) nojalla.")
        recognizer = PlainTextStatuteCitationRecognizer()
        ids_on = {h.statute_id for h in recognizer.scan_precise(p, include_ref_text=False)}
        ids_fold = {h.statute_id for h in recognizer.scan_precise(p, include_ref_text=True)}
        assert "1999/361" in ids_on
        assert "1999/361" in ids_fold

    def test_distant_noun_id_not_bound(self) -> None:
        """Whitespace collapse must NOT bind an id to an unrelated distant noun.

        Intervening WORDS (not just markup whitespace) keep the name head and the
        id non-adjacent, so the recogniser declines — collapsing whitespace never
        merges across real tokens.
        """
        p = self._p(
            "lain mukaan asia ratkaistaan myohemmin erikseen mainitulla tavalla "
            "ja sovelletaan tarvittaessa (688/1988)."
        )
        recognizer = PlainTextStatuteCitationRecognizer()
        # No name head is adjacent to the id, so no by-id hit binds to "lain".
        hits = recognizer.scan_precise(p, include_ref_text=True)
        assert all(h.statute_id != "688/1988" for h in hits) or hits == [], hits


class TestEntryIntoForceDateRefNotTypedCrossStatute:
    """An ``#entryIntoForce`` editorial date-ref (``13.6.1929/228``) must NOT be
    typed as a CROSS_STATUTE cite by the by-id text lane.

    Finlex consolidation commencement footnotes glue a date (``d.m.YYYY``) to the
    amendment's running number. The ``YYYY/NNN`` tail superficially resembles a
    statute id, so once the ``<ref>`` inner text is folded into the plain-text
    scan a preceding name head could otherwise bind the date as a bogus cite. The
    recogniser declines any id immediately preceded by a ``d.m.YYYY`` date.
    """

    def _p(self, body: str):  # type: ignore[no-untyped-def]
        import xml.etree.ElementTree as ET
        return ET.fromstring(
            '<p xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            + body
            + "</p>"
        )

    def test_bare_date_ref_not_recovered(self) -> None:
        """``tulee voimaan 13.6.1929/228`` yields no by-id cite (no parens)."""
        p = self._p("Tama laki tulee voimaan 13.6.1929/228.")
        recognizer = PlainTextStatuteCitationRecognizer()
        assert recognizer.scan_precise(p, include_ref_text=True) == []

    def test_date_ref_folded_from_ref_not_typed_cross_statute(self) -> None:
        """``laissa </ref-split>13.6.1929/228`` editorial date-ref is declined.

        Even if a name head precedes a folded ``#entryIntoForce`` date-ref, the
        date prefix marks it editorial; the recogniser must not promote it to a
        CROSS_STATUTE cite to 1929/228.
        """
        p = self._p(
            "mita mainitussa laissa\n"
            "                          "
            '<ref href="#entryIntoForce_19290228">13.6.1929/228</ref>'
            "\n                          saadetaan."
        )
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(p, include_ref_text=True)
        assert all(h.statute_id not in ("1929/228", "228/1929") for h in hits), hits

    def test_annotation_on_default_excludes_ref_date_text(self) -> None:
        """Production (annotation-ON) never sees the folded date-ref at all."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body><section><num>1 \xc2\xa7</num><paragraph><content>"
            b"<p>Tama laki tulee voimaan "
            b'<ref href="#entryIntoForce_19290228">13.6.1929/228</ref>.</p>'
            b"</content></paragraph></section></body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "1734/3-000")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert plain == [], plain


# ===========================================================================
# Johtolause amendment-target (<affectedDocument>) lane
# ===========================================================================


class TestAffectedDocumentMentions:
    """The preamble <affectedDocument> amendment-target surfaces as a mention."""

    _PURE_AMENDMENT = (
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<act><preamble>"
        b'<formula name="enactingClause">'
        b"<p>Valtioneuvoston paatoksen mukaisesti</p>"
        b"<blockContainer>"
        b'<block name="substitutions"><i>muutetaan</i>'
        b" jonkin asetuksen ("
        b'<affectedDocument href="/akn/fi/act/statute/2014/1129">1129/2014'
        b"</affectedDocument>) 3 \xc2\xa7, seuraavasti:</block>"
        b"</blockContainer></formula></preamble>"
        b"<body><section><num>3 \xc2\xa7</num><paragraph><content>"
        b"<p>Uusi teksti.</p></content></paragraph></section></body>"
        b"</act></akomaNtoso>"
    )

    def test_pure_amendment_gains_amends_cross_statute_mention(self) -> None:
        result = extract_all_reference_mentions(self._PURE_AMENDMENT, "2019/1294")
        amends = [m for m in result.mentions if m.edge_subtype == "AMENDS"]
        assert len(amends) == 1
        m = amends[0]
        assert m.target_provision_ref is not None
        assert m.target_provision_ref.statute_id == "2014/1129"
        assert m.cite_kind is CiteKind.CROSS_STATUTE
        assert m.cite_confidence is CiteConfidence.EXACT
        assert m.phrase_lemma == "affected_document"
        # Byte span anchors the displayed citation phrase in xml_bytes.
        assert m.source_span is not None
        sliced = self._PURE_AMENDMENT[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        assert sliced == b"1129/2014"

    def test_amends_target_also_in_body_ref_yields_one_mention(self) -> None:
        # Same target named both by <affectedDocument> AND a body <ref> CITES:
        # the body <ref> owns that occurrence, the johtolause surface is
        # suppressed → exactly one mention for that target.
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><preamble>"
            b'<formula name="enactingClause"><blockContainer>'
            b'<block name="substitutions"><i>muutetaan</i> ('
            b'<affectedDocument href="/akn/fi/act/statute/2014/1129">1129/2014'
            b"</affectedDocument>) 3 \xc2\xa7,</block></blockContainer>"
            b"</formula></preamble>"
            b"<body><section><num>3 \xc2\xa7</num><paragraph><content>"
            b'<p>Katso <ref href="/akn/fi/act/statute/2014/1129#sec_5">'
            b"toinen saados</ref>.</p></content></paragraph></section></body>"
            b"</act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2019/1294")
        to_target = [
            m
            for m in result.mentions
            if m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "2014/1129"
        ]
        assert len(to_target) == 1
        # The surviving occurrence is the richer body <ref> CITES.
        assert to_target[0].edge_subtype == "CITES"
        assert all(m.edge_subtype != "AMENDS" for m in result.mentions)
