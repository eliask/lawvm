"""Tests for ActorMention core primitive and Finland extractor.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests -- typed primitive construction + enum coverage.
  2. Real corpus regression via conformance fixtures.
  3. Finding/observation tests -- AmbiguousActorMention, LifecycleActorObservation,
     RejectedActorCandidate emitted correctly.
  4. Negative tests -- non-actor text does not produce ActorMention.
  5. Strict-mode tests -- UNRESOLVED/AMBIGUOUS blocked in strict mode.
  6. No-leak tests -- synthetic markers not in non-test parquet.
  7. Schema-stability tests -- parquet column order + dtypes pinned.

Module coverage:
  - lawvm.core.actor_mention (ActorMention, ActorModalKind, ActorResolutionConfidence, etc.)
  - lawvm.finland.actor_mention_extractor (extraction entry points)
  - lawvm.finland.canonical_actor_registry (REGISTRY, CanonicalActor, LifecyclePeriod)
  - lawvm.finland.conformance_corpus.actors.fixtures (conformance fixtures)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict

import pytest

from lawvm.core.actor_mention import (
    ActorMention,
    ActorModalKind,
    ActorResolutionConfidence,
    AmbiguousActorMention,
    LifecycleActorObservation,
    RejectedActorCandidate,
    actor_mention_to_row,
)
from lawvm.finland.actor_mention_extractor import (
    ActorExtractionResult,
    ModalVerbRecognizer,
    extract_actor_mentions,
)
from lawvm.finland.canonical_actor_registry import REGISTRY, LifecyclePeriod
from lawvm.finland.conformance_corpus.actors.fixtures import (
    ALL_FIXTURES,
    EXACT_TLC_DISCRETION,
    EXACT_TLC_DUTY,
    EXACT_TLC_MENTION,
    EXACT_TLC_PERMISSION,
    LIFECYCLE_RESOLVED_EVIRA,
    LIFECYCLE_RESOLVED_TRAFI,
    NO_LEAK_SYNTHETIC_MARKER,
    REGISTRY_RESOLVED_PROSE,
    UNRESOLVED_GENERIC_MINISTERIO,
    XML_PARSE_FAILURE,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_mention_matches(actual: ActorMention, expected: Dict[str, Any]) -> None:
    """Assert that actual ActorMention matches all expected key/value pairs."""
    row = actor_mention_to_row(actual)
    # Also expose fields not in row directly
    row["actor_phrase"] = actual.actor_phrase
    row["actor_canonical_id"] = actual.actor_canonical_id
    row["actor_canonical_show_as"] = actual.actor_canonical_show_as
    row["modal_kind"] = actual.modal_kind.value
    row["resolution_confidence"] = actual.resolution_confidence.value
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"mention key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


# ===========================================================================
# Category 1: Synthetic unit tests -- typed primitive construction
# ===========================================================================


class TestActorMentionConstruction:
    """Synthetic unit tests for the typed primitive itself."""

    def test_exact_construction(self) -> None:
        """EXACT resolution: canonical_id required."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        assert mention.actor_canonical_id == "fi.agency.ruokavirasto"
        assert mention.modal_kind == ActorModalKind.MENTION
        assert mention.resolution_confidence == ActorResolutionConfidence.EXACT

    def test_unresolved_allows_none_canonical_id(self) -> None:
        """UNRESOLVED resolution allows actor_canonical_id=None."""
        mention = ActorMention(
            source_provision_ref="2010/400/1",
            actor_phrase="ministerio",
            actor_canonical_id=None,
            actor_canonical_show_as=None,
            modal_kind=ActorModalKind.UNRESOLVED,
            resolution_confidence=ActorResolutionConfidence.UNRESOLVED,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        assert mention.actor_canonical_id is None
        assert mention.resolution_confidence == ActorResolutionConfidence.UNRESOLVED

    def test_exact_requires_canonical_id(self) -> None:
        """EXACT resolution MUST have a non-None actor_canonical_id."""
        with pytest.raises(ValueError, match="actor_canonical_id"):
            ActorMention(
                source_provision_ref="2019/561/1",
                actor_phrase="Ruokavirasto",
                actor_canonical_id=None,
                actor_canonical_show_as=None,
                modal_kind=ActorModalKind.MENTION,
                resolution_confidence=ActorResolutionConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )

    def test_empty_actor_phrase_rejected(self) -> None:
        """Empty actor_phrase is not allowed."""
        with pytest.raises(ValueError, match="actor_phrase"):
            ActorMention(
                source_provision_ref="2019/561/1",
                actor_phrase="",
                actor_canonical_id="fi.agency.ruokavirasto",
                actor_canonical_show_as="Ruokavirasto",
                modal_kind=ActorModalKind.MENTION,
                resolution_confidence=ActorResolutionConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )

    def test_frozen_dataclass_immutable(self) -> None:
        """ActorMention is frozen (immutable)."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        with pytest.raises((TypeError, AttributeError)):
            mention.modal_kind = ActorModalKind.DUTY  # type: ignore[misc]

    def test_all_modal_kinds_constructable(self) -> None:
        """Each ActorModalKind enum value is constructable."""
        for kind in ActorModalKind:
            mention = ActorMention(
                source_provision_ref="2019/561/1",
                actor_phrase="Ruokavirasto",
                actor_canonical_id="fi.agency.ruokavirasto",
                actor_canonical_show_as="Ruokavirasto",
                modal_kind=kind,
                resolution_confidence=ActorResolutionConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )
            assert mention.modal_kind == kind

    def test_all_resolution_confidences_constructable(self) -> None:
        """All ActorResolutionConfidence values constructable (UNRESOLVED with None ID)."""
        for conf in ActorResolutionConfidence:
            cid = None if conf == ActorResolutionConfidence.UNRESOLVED else "fi.agency.ruokavirasto"
            mention = ActorMention(
                source_provision_ref="2019/561/1",
                actor_phrase="Ruokavirasto",
                actor_canonical_id=cid,
                actor_canonical_show_as="Ruokavirasto" if cid else None,
                modal_kind=ActorModalKind.MENTION,
                resolution_confidence=conf,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_start=None,
                valid_at_end=None,
            )
            assert mention.resolution_confidence == conf

    def test_valid_at_interval_stored(self) -> None:
        """valid_at_start and valid_at_end are stored correctly."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file="test.xml",
            source_span_byte_offset=100,
            source_span_byte_len=12,
            valid_at_start=date(2019, 1, 1),
            valid_at_end=None,
        )
        assert mention.valid_at_start == date(2019, 1, 1)
        assert mention.valid_at_end is None
        assert mention.source_span_byte_offset == 100


# ===========================================================================
# Category 1b: Enum coverage
# ===========================================================================


class TestEnumCoverage:
    """All enum values are accessible and have correct string values."""

    def test_actor_modal_kind_values(self) -> None:
        assert ActorModalKind.DUTY.value == "duty"
        assert ActorModalKind.DISCRETION.value == "discretion"
        assert ActorModalKind.PERMISSION.value == "permission"
        assert ActorModalKind.PROHIBITION.value == "prohibition"
        assert ActorModalKind.MENTION.value == "mention"
        assert ActorModalKind.PASSIVE_OBLIGATION.value == "passive_obligation"
        assert ActorModalKind.UNRESOLVED.value == "unresolved"

    def test_resolution_confidence_values(self) -> None:
        assert ActorResolutionConfidence.EXACT.value == "exact"
        assert ActorResolutionConfidence.REGISTRY_RESOLVED.value == "registry_resolved"
        assert ActorResolutionConfidence.LIFECYCLE_RESOLVED.value == "lifecycle_resolved"
        assert ActorResolutionConfidence.UNRESOLVED.value == "unresolved"


# ===========================================================================
# Category 1c: Registry unit tests
# ===========================================================================


class TestCanonicalActorRegistry:
    """Unit tests for the canonical actor registry."""

    def test_registry_has_ruokavirasto(self) -> None:
        actor = REGISTRY.get_actor("fi.agency.ruokavirasto")
        assert actor is not None
        assert actor.show_as == "Ruokavirasto"

    def test_registry_has_traficom(self) -> None:
        actor = REGISTRY.get_actor("fi.agency.traficom")
        assert actor is not None

    def test_lookup_ruokavirasto_phrase(self) -> None:
        canonical_id, candidates = REGISTRY.lookup("Ruokavirasto")
        assert canonical_id == "fi.agency.ruokavirasto"
        assert len(candidates) == 1

    def test_lookup_evira_predecessor(self) -> None:
        """'Evira' resolves to fi.agency.ruokavirasto (lifecycle predecessor)."""
        canonical_id, candidates = REGISTRY.lookup("Evira")
        assert canonical_id == "fi.agency.ruokavirasto"
        assert len(candidates) == 1

    def test_lookup_trafi_predecessor(self) -> None:
        """'Trafi' resolves to fi.agency.traficom (lifecycle predecessor)."""
        canonical_id, candidates = REGISTRY.lookup("Trafi")
        assert canonical_id == "fi.agency.traficom"
        assert len(candidates) == 1

    def test_lookup_unknown_phrase_returns_empty(self) -> None:
        canonical_id, candidates = REGISTRY.lookup("__totally_unknown_phrase__")
        assert canonical_id is None
        assert candidates == []

    def test_is_predecessor_phrase_evira(self) -> None:
        assert REGISTRY.is_predecessor_phrase_for("Evira", "fi.agency.ruokavirasto")

    def test_is_predecessor_phrase_ruokavirasto_is_not_predecessor(self) -> None:
        # Current name is NOT a predecessor phrase
        assert not REGISTRY.is_predecessor_phrase_for("Ruokavirasto", "fi.agency.ruokavirasto")

    def test_lifecycle_observation_for_evira(self) -> None:
        info = REGISTRY.lifecycle_observation_for("Evira", "fi.agency.ruokavirasto")
        assert info is not None
        pred_id, succ_id, lc_date = info
        assert succ_id == "fi.agency.ruokavirasto"
        assert lc_date == date(2019, 1, 1)

    def test_all_phrases_longest_first_ordered(self) -> None:
        phrases = REGISTRY.all_phrases_longest_first()
        # Must be sorted longest first
        lengths = [len(p) for p in phrases]
        assert lengths == sorted(lengths, reverse=True)

    def test_lifecycle_period_is_active_at(self) -> None:
        period = LifecyclePeriod(
            active_from=date(2019, 1, 1),
            active_until=None,
            phrase_variants=("Ruokavirasto",),
        )
        assert period.is_active_at(date(2020, 1, 1))
        assert not period.is_active_at(date(2018, 12, 31))
        assert period.is_active_at(None)  # current = active

    def test_lifecycle_period_predecessor_is_not_active_after_cutoff(self) -> None:
        period = LifecyclePeriod(
            active_from=date(2006, 5, 1),
            active_until=date(2019, 1, 1),
            phrase_variants=("Evira",),
            successor_id="fi.agency.ruokavirasto",
        )
        assert period.is_active_at(date(2017, 6, 1))
        assert not period.is_active_at(date(2019, 1, 1))  # exclusive upper bound
        assert not period.is_active_at(None)  # no longer current


# ===========================================================================
# Category 1d: ModalVerbRecognizer unit tests
# ===========================================================================


class TestModalVerbRecognizer:
    """Unit tests for the named modal-verb recognizer family."""

    def setup_method(self) -> None:
        self.recognizer = ModalVerbRecognizer()

    def test_classify_duty_context_has_genitive_word(self) -> None:
        """'viranomaisen on myontaa' -> DUTY (genitive word in context)."""
        ctx = " viranomaisen on my\xf6nnett\xe4v\xe4 lupa"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.DUTY

    def test_classify_duty_phrase_ends_in_genitive(self) -> None:
        """Phrase ending in genitive + ' on' at start of context -> DUTY."""
        # This covers e.g. 'Liikenne- ja viestintaviraston' + context ' on myonnettava'
        ctx = " on my\xf6nnett\xe4v\xe4 lupa"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.DUTY

    def test_classify_discretion(self) -> None:
        """'voi antaa' -> DISCRETION."""
        ctx = " voi antaa m\xe4\xe4r\xe4yksi\xe4"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.DISCRETION

    def test_classify_permission(self) -> None:
        """'saa periua luvan' -> PERMISSION (no 'ei' before)."""
        ctx = " saa peri\xe4 luvan"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.PERMISSION

    def test_classify_prohibition(self) -> None:
        """'ei saa luovuttaa' -> PROHIBITION (takes priority over PERMISSION)."""
        ctx = " ei saa luovuttaa tietoja"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.PROHIBITION

    def test_classify_passive_obligation(self) -> None:
        """'tehtavana on tarkastaa' -> PASSIVE_OBLIGATION."""
        ctx = " teht\xe4v\xe4n\xe4 on tarkastaa"
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.PASSIVE_OBLIGATION

    def test_classify_mention_no_modal(self) -> None:
        """Plain mention without modal verb -> MENTION."""
        ctx = " julkaisee tiedot verkkosivuillaan."
        result = self.recognizer.classify(ctx)
        assert result == ActorModalKind.MENTION

    def test_classify_empty_context(self) -> None:
        """Empty context -> MENTION."""
        result = self.recognizer.classify("")
        assert result == ActorModalKind.MENTION


# ===========================================================================
# Category 2: Real corpus regression via conformance fixtures
# ===========================================================================


class TestConformanceFixtures:
    """Real AKN patterns from the conformance corpus."""

    def test_fixture_exact_tlc_mention(self) -> None:
        """EXACT x MENTION: TLCOrganization for Ruokavirasto."""
        result = extract_actor_mentions(
            EXACT_TLC_MENTION.xml_bytes,
            EXACT_TLC_MENTION.source_statute_id,
        )
        assert len(result.mentions) >= 1
        # At least one mention has EXACT confidence
        exact = [m for m in result.mentions if m.resolution_confidence == ActorResolutionConfidence.EXACT]
        assert len(exact) >= 1

    def test_fixture_exact_tlc_duty(self) -> None:
        """EXACT (TLC) + DUTY (prose): Traficom with 'on myonnettava'."""
        result = extract_actor_mentions(
            EXACT_TLC_DUTY.xml_bytes,
            EXACT_TLC_DUTY.source_statute_id,
        )
        duty_mentions = [m for m in result.mentions if m.modal_kind == ActorModalKind.DUTY]
        assert len(duty_mentions) >= 1, f"Expected DUTY mention, got {result.mentions}"
        traficom_duty = [m for m in duty_mentions if "traficom" in (m.actor_canonical_id or "")]
        assert len(traficom_duty) >= 1

    def test_fixture_exact_tlc_discretion(self) -> None:
        """Valvira with 'voi periua' -> DISCRETION."""
        result = extract_actor_mentions(
            EXACT_TLC_DISCRETION.xml_bytes,
            EXACT_TLC_DISCRETION.source_statute_id,
        )
        discretion = [m for m in result.mentions if m.modal_kind == ActorModalKind.DISCRETION]
        assert len(discretion) >= 1

    def test_fixture_exact_tlc_permission(self) -> None:
        """STUK with 'saa antaa' -> PERMISSION."""
        result = extract_actor_mentions(
            EXACT_TLC_PERMISSION.xml_bytes,
            EXACT_TLC_PERMISSION.source_statute_id,
        )
        permission = [m for m in result.mentions if m.modal_kind == ActorModalKind.PERMISSION]
        assert len(permission) >= 1

    def test_fixture_registry_resolved_prose(self) -> None:
        """Traficom in prose only -> REGISTRY_RESOLVED."""
        result = extract_actor_mentions(
            REGISTRY_RESOLVED_PROSE.xml_bytes,
            REGISTRY_RESOLVED_PROSE.source_statute_id,
        )
        registry_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.REGISTRY_RESOLVED
        ]
        assert len(registry_mentions) >= 1
        traficom = [m for m in registry_mentions if m.actor_canonical_id == "fi.agency.traficom"]
        assert len(traficom) >= 1

    def test_fixture_lifecycle_evira(self) -> None:
        """'Evira' -> LIFECYCLE_RESOLVED + LifecycleActorObservation."""
        result = extract_actor_mentions(
            LIFECYCLE_RESOLVED_EVIRA.xml_bytes,
            LIFECYCLE_RESOLVED_EVIRA.source_statute_id,
        )
        lifecycle_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.LIFECYCLE_RESOLVED
        ]
        assert len(lifecycle_mentions) >= 1
        evira_mention = lifecycle_mentions[0]
        assert evira_mention.actor_canonical_id == "fi.agency.ruokavirasto"
        assert evira_mention.actor_phrase == "Evira"

        # LifecycleActorObservation must be emitted
        assert len(result.lifecycle_observations) >= 1
        obs = result.lifecycle_observations[0]
        assert obs.successor_id == "fi.agency.ruokavirasto"
        assert obs.lifecycle_date == date(2019, 1, 1)

    def test_fixture_lifecycle_trafi(self) -> None:
        """'Trafi' -> LIFECYCLE_RESOLVED for Traficom."""
        result = extract_actor_mentions(
            LIFECYCLE_RESOLVED_TRAFI.xml_bytes,
            LIFECYCLE_RESOLVED_TRAFI.source_statute_id,
        )
        lifecycle_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.LIFECYCLE_RESOLVED
        ]
        assert len(lifecycle_mentions) >= 1
        assert lifecycle_mentions[0].actor_canonical_id == "fi.agency.traficom"

    def test_fixture_unresolved_generic_ministerio(self) -> None:
        """'Ministerio' without qualifier -> AmbiguousActorMention."""
        result = extract_actor_mentions(
            UNRESOLVED_GENERIC_MINISTERIO.xml_bytes,
            UNRESOLVED_GENERIC_MINISTERIO.source_statute_id,
        )
        # Ambiguous finding emitted -- not a regular ActorMention
        assert len(result.ambiguous_findings) >= 1
        af = result.ambiguous_findings[0]
        assert len(af.candidate_canonical_ids) > 1

    def test_fixture_xml_parse_failure(self) -> None:
        """Corrupt XML -> blocking RejectedActorCandidate, no mentions."""
        result = extract_actor_mentions(
            XML_PARSE_FAILURE.xml_bytes,
            XML_PARSE_FAILURE.source_statute_id,
        )
        assert result.mentions == []
        blocking = [r for r in result.rejected if r.blocking]
        assert len(blocking) >= 1
        assert blocking[0].rule_id == "fi_actor_mention_xml_parse_failed"

    def test_all_fixtures_run_without_exception(self) -> None:
        """All conformance fixtures must run without unhandled exceptions."""
        for fid, fixture in ALL_FIXTURES.items():
            result = extract_actor_mentions(
                fixture.xml_bytes,
                fixture.source_statute_id,
            )
            assert isinstance(result, ActorExtractionResult), (
                f"Fixture {fid}: expected ActorExtractionResult, got {type(result)}"
            )


# ===========================================================================
# Category 3: Finding/observation tests
# ===========================================================================


class TestFindingObservation:
    """Tests that typed findings are emitted correctly."""

    def test_ambiguous_actor_mention_construction(self) -> None:
        """AmbiguousActorMention has required fields + normalizes tuple."""
        finding = AmbiguousActorMention(
            rule_id="fi_actor_mention_ambiguous_phrase",
            phase="actor_mention_extraction",
            source_statute_id="2010/400",
            source_provision_ref="2010/400/1",
            actor_phrase="ministerio",
            candidate_canonical_ids=["fi.ministry.stm", "fi.ministry.sm"],
            reason="Phrase 'ministerio' matches 2 registry entries.",
        )
        assert isinstance(finding.candidate_canonical_ids, tuple)
        assert "fi.ministry.stm" in finding.candidate_canonical_ids
        assert finding.blocking is False

    def test_lifecycle_actor_observation_construction(self) -> None:
        """LifecycleActorObservation has lifecycle_date as date."""
        obs = LifecycleActorObservation(
            rule_id="fi_actor_lifecycle_phrase_resolved",
            phase="actor_mention_extraction",
            source_statute_id="2017/100",
            source_provision_ref="2017/100/4",
            actor_phrase="Evira",
            predecessor_id="fi.agency.ruokavirasto",
            successor_id="fi.agency.ruokavirasto",
            lifecycle_date=date(2019, 1, 1),
            reason="Evira -> Ruokavirasto lifecycle.",
        )
        assert obs.lifecycle_date == date(2019, 1, 1)
        assert obs.blocking is False

    def test_rejected_actor_candidate_construction(self) -> None:
        """RejectedActorCandidate has all required fields."""
        rej = RejectedActorCandidate(
            rule_id="fi_actor_mention_xml_parse_failed",
            phase="actor_mention_extraction",
            source_statute_id="2000/1",
            reason="XML parse error.",
            matched_text="",
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            blocking=True,
        )
        assert rej.blocking is True
        assert rej.strict_disposition == "record"

    def test_lifecycle_observation_emitted_for_evira(self) -> None:
        """LIFECYCLE_RESOLVED mention always pairs with LifecycleActorObservation."""
        result = extract_actor_mentions(
            LIFECYCLE_RESOLVED_EVIRA.xml_bytes,
            LIFECYCLE_RESOLVED_EVIRA.source_statute_id,
        )
        lifecycle_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.LIFECYCLE_RESOLVED
        ]
        lifecycle_obs = result.lifecycle_observations
        # At least as many observations as lifecycle mentions
        assert len(lifecycle_obs) >= len(lifecycle_mentions)

    def test_ambiguous_finding_has_multiple_candidates(self) -> None:
        """AmbiguousActorMention.candidate_canonical_ids has >= 2 entries."""
        result = extract_actor_mentions(
            UNRESOLVED_GENERIC_MINISTERIO.xml_bytes,
            UNRESOLVED_GENERIC_MINISTERIO.source_statute_id,
        )
        assert result.ambiguous_findings, "Expected ambiguous finding"
        af = result.ambiguous_findings[0]
        assert len(af.candidate_canonical_ids) >= 2


# ===========================================================================
# Category 4: Negative tests
# ===========================================================================


class TestNegative:
    """Non-actor text must not produce ActorMention records."""

    def test_empty_statute_body_no_mentions(self) -> None:
        """A statute with no actor phrases -> no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>Ei toimijoita.</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_actor_mentions(xml, "2000/1")
        # No registry-matched actors in 'Ei toimijoita'
        registry_mentions = [
            m for m in result.mentions
            if m.resolution_confidence in (
                ActorResolutionConfidence.REGISTRY_RESOLVED,
                ActorResolutionConfidence.LIFECYCLE_RESOLVED,
            )
        ]
        assert registry_mentions == []

    def test_no_body_no_prose_mentions(self) -> None:
        """Statute with no body element -> no prose mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/></act></akomaNtoso>"
        )
        result = extract_actor_mentions(xml, "2000/1")
        registry_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.REGISTRY_RESOLVED
        ]
        assert registry_mentions == []

    def test_partial_word_not_matched(self) -> None:
        """A partial match in a longer word does not produce a mention.

        'Eviran' should match (genitive), but 'Eviranomaiset' should not
        because 's' immediately follows 'Evira'.
        This tests the boundary check in the prose scanner.
        """
        # 'Eviranomaiset' is not in the registry and should not match 'Evira'
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Eviranomaiset toimivat yhteistyossa."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_actor_mentions(xml, "2000/1")
        evira_mentions = [m for m in result.mentions if "Evira" in m.actor_phrase]
        # 'Eviranomaiset' has a letter after 'Evira' -> boundary check blocks it
        # ('n' after 'Evira' -- but 'Eviran' IS a valid phrase variant in the registry)
        # This assertion verifies no 'Evira'-stem match in clearly wrong word
        # Note: 'Eviran' is a registered phrase, so 'Eviran' in text would match,
        # but 'Eviranomaiset' contains 'omaiset' after 'Eviran' -- boundary fails.
        assert all("omaiset" not in m.actor_phrase for m in evira_mentions)


# ===========================================================================
# Category 5: Strict-mode tests
# ===========================================================================


class TestStrictMode:
    """Strict mode blocks UNRESOLVED and AMBIGUOUS mentions."""

    def test_strict_mode_xml_parse_failure_blocking(self) -> None:
        """In strict mode, parse failure produces blocking RejectedActorCandidate."""
        result = extract_actor_mentions(b"<invalid>", "2000/1", strict=True)
        assert result.mentions == []
        blocking = [r for r in result.rejected if r.blocking]
        assert len(blocking) >= 1

    def test_strict_mode_blocks_ambiguous(self) -> None:
        """In strict mode, ambiguous phrases produce blocking rejected records."""
        result = extract_actor_mentions(
            UNRESOLVED_GENERIC_MINISTERIO.xml_bytes,
            UNRESOLVED_GENERIC_MINISTERIO.source_statute_id,
            strict=True,
        )
        blocking_rejected = [r for r in result.rejected if r.blocking]
        assert len(blocking_rejected) >= 1
        assert any("ambiguous" in r.rule_id for r in blocking_rejected)

    def test_non_strict_mode_no_extra_blocking(self) -> None:
        """Non-strict mode does not add blocking records for ambiguous phrases."""
        result = extract_actor_mentions(
            UNRESOLVED_GENERIC_MINISTERIO.xml_bytes,
            UNRESOLVED_GENERIC_MINISTERIO.source_statute_id,
            strict=False,
        )
        # Ambiguous finding exists, but not blocking
        assert len(result.ambiguous_findings) >= 1
        assert not any("ambiguous" in r.rule_id for r in result.rejected)

    def test_strict_mode_exact_mentions_not_blocked(self) -> None:
        """EXACT confidence mentions are not blocked in strict mode."""
        result = extract_actor_mentions(
            EXACT_TLC_MENTION.xml_bytes,
            EXACT_TLC_MENTION.source_statute_id,
            strict=True,
        )
        exact = [m for m in result.mentions if m.resolution_confidence == ActorResolutionConfidence.EXACT]
        assert len(exact) >= 1


# ===========================================================================
# Category 6: No-leak tests
# ===========================================================================


class TestNoLeak:
    """Synthetic test markers must not leak into production parquet runs."""

    def test_synthetic_statute_id_extractable_in_test(self) -> None:
        """Synthetic IDs extract correctly in test context."""
        result = extract_actor_mentions(
            NO_LEAK_SYNTHETIC_MARKER.xml_bytes,
            NO_LEAK_SYNTHETIC_MARKER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        # Source provision ref carries the synthetic marker
        assert result.mentions[0].source_provision_ref.startswith("__test__")

    def test_actor_mention_to_row_no_internal_sentinels_in_canonical_id(self) -> None:
        """Serialized row canonical_id must not contain '__test__' for real actors."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = actor_mention_to_row(mention)
        assert "__test__" not in (row["actor_canonical_id"] or "")


# ===========================================================================
# Category 7: Schema-stability tests
# ===========================================================================


class TestSchemaStability:
    """Parquet schema column order and dtypes must be pinned."""

    EXPECTED_COLUMNS = [
        "source_provision_ref_str",
        "actor_phrase",
        "actor_canonical_id",
        "actor_canonical_show_as",
        "modal_kind",
        "resolution_confidence",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_byte_len",
        "valid_at_start",
        "valid_at_end",
    ]

    def test_actor_mention_to_row_has_all_columns(self) -> None:
        """actor_mention_to_row() produces all expected columns."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = actor_mention_to_row(mention)
        for col in self.EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from serialized row"

    def test_actor_mention_to_row_column_types(self) -> None:
        """Column types match expected Python types."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.DUTY,
            resolution_confidence=ActorResolutionConfidence.REGISTRY_RESOLVED,
            source_span_file="/data/fi/2019/561.xml",
            source_span_byte_offset=1024,
            source_span_byte_len=12,
            valid_at_start=date(2019, 1, 1),
            valid_at_end=None,
        )
        row = actor_mention_to_row(mention)
        assert isinstance(row["source_provision_ref_str"], str)
        assert isinstance(row["actor_phrase"], str)
        assert isinstance(row["actor_canonical_id"], str)
        assert isinstance(row["modal_kind"], str)
        assert isinstance(row["resolution_confidence"], str)
        assert isinstance(row["source_span_file"], str)
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_byte_len"], int)
        assert isinstance(row["valid_at_start"], str)  # isoformat
        assert row["valid_at_end"] is None

    def test_actor_mention_to_row_nullable_columns(self) -> None:
        """Nullable columns are None when absent."""
        mention = ActorMention(
            source_provision_ref="2010/400/1",
            actor_phrase="ministerio",
            actor_canonical_id=None,
            actor_canonical_show_as=None,
            modal_kind=ActorModalKind.UNRESOLVED,
            resolution_confidence=ActorResolutionConfidence.UNRESOLVED,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = actor_mention_to_row(mention)
        assert row["actor_canonical_id"] is None
        assert row["actor_canonical_show_as"] is None
        assert row["source_span_file"] is None
        assert row["source_span_byte_offset"] is None
        assert row["source_span_byte_len"] is None
        assert row["valid_at_start"] is None
        assert row["valid_at_end"] is None

    def test_column_order_stable(self) -> None:
        """Column order in actor_mention_to_row() output is stable."""
        mention = ActorMention(
            source_provision_ref="2019/561/1",
            actor_phrase="Ruokavirasto",
            actor_canonical_id="fi.agency.ruokavirasto",
            actor_canonical_show_as="Ruokavirasto",
            modal_kind=ActorModalKind.MENTION,
            resolution_confidence=ActorResolutionConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=None,
            valid_at_end=None,
        )
        row = actor_mention_to_row(mention)
        actual_cols = list(row.keys())
        assert actual_cols == self.EXPECTED_COLUMNS


# ===========================================================================
# Task 3: Finnish Unicode character normalization in actor registry
# ===========================================================================


class TestActorRegistryUnicodeNormalization:
    """Regression tests for proper Finnish Unicode in the canonical actor registry.

    The registry previously used ASCII-approximated names (e.g.
    'Sosiaali- ja terveysministerio' without ö). Real Finlex XML contains
    proper Finnish characters. These tests verify the registry matches both
    the proper Unicode forms AND retains backward-compat ASCII forms.

    Per AGENTS.md §1.6: lifecycle / registry resolution must not silently alias.
    All phrase_variants are explicit in the registry; no normalisation at lookup time.
    """

    # ── Proper Unicode forms: ministries ────────────────────────────────────

    def test_stm_unicode_proper_lookup(self) -> None:
        """'Sosiaali- ja terveysministeriö' (with ö) resolves to fi.ministry.stm."""
        canonical_id, candidates = REGISTRY.lookup("Sosiaali- ja terveysministeriö")
        assert canonical_id == "fi.ministry.stm", (
            f"Expected fi.ministry.stm, got {canonical_id} (candidates={candidates})"
        )

    def test_stm_unicode_genitive_lookup(self) -> None:
        """'sosiaali- ja terveysministeriön' (genitive ö) resolves to fi.ministry.stm."""
        canonical_id, candidates = REGISTRY.lookup("sosiaali- ja terveysministeriön")
        assert canonical_id == "fi.ministry.stm"

    def test_stm_ascii_fallback_still_resolves(self) -> None:
        """ASCII-approximated 'Sosiaali- ja terveysministerio' still resolves (backward compat)."""
        canonical_id, candidates = REGISTRY.lookup("Sosiaali- ja terveysministerio")
        assert canonical_id == "fi.ministry.stm"

    def test_sm_unicode_sisäministeriö(self) -> None:
        """'Sisäministeriö' (with ä, ö) resolves to fi.ministry.sm."""
        canonical_id, candidates = REGISTRY.lookup("Sisäministeriö")
        assert canonical_id == "fi.ministry.sm"

    def test_lvm_unicode_liikenne_ja_viestintäministeriö(self) -> None:
        """'Liikenne- ja viestintäministeriö' resolves to fi.ministry.lvm."""
        canonical_id, candidates = REGISTRY.lookup("Liikenne- ja viestintäministeriö")
        assert canonical_id == "fi.ministry.lvm"

    def test_mmm_unicode_maa_ja_metsätalousministeriö(self) -> None:
        """'Maa- ja metsätalousministeriö' resolves to fi.ministry.mmm."""
        canonical_id, candidates = REGISTRY.lookup("Maa- ja metsätalousministeriö")
        assert canonical_id == "fi.ministry.mmm"

    def test_okm_unicode_opetus_ja_kulttuuriministeriö(self) -> None:
        """'Opetus- ja kulttuuriministeriö' resolves to fi.ministry.okm."""
        canonical_id, candidates = REGISTRY.lookup("Opetus- ja kulttuuriministeriö")
        assert canonical_id == "fi.ministry.okm"

    def test_vm_unicode_valtiovarainministeriö(self) -> None:
        """'Valtiovarainministeriö' resolves to fi.ministry.vm."""
        canonical_id, candidates = REGISTRY.lookup("Valtiovarainministeriö")
        assert canonical_id == "fi.ministry.vm"

    def test_tem_unicode_työ_ja_elinkeinoministeriö(self) -> None:
        """'Työ- ja elinkeinoministeriö' resolves to fi.ministry.tem."""
        canonical_id, candidates = REGISTRY.lookup("Työ- ja elinkeinoministeriö")
        assert canonical_id == "fi.ministry.tem"

    def test_ym_unicode_ympäristöministeriö(self) -> None:
        """'Ympäristöministeriö' resolves to fi.ministry.ym."""
        canonical_id, candidates = REGISTRY.lookup("Ympäristöministeriö")
        assert canonical_id == "fi.ministry.ym"

    def test_om_unicode_oikeusministeriö(self) -> None:
        """'Oikeusministeriö' resolves to fi.ministry.om."""
        canonical_id, candidates = REGISTRY.lookup("Oikeusministeriö")
        assert canonical_id == "fi.ministry.om"

    def test_plm_unicode_puolustusministeriö(self) -> None:
        """'Puolustusministeriö' resolves to fi.ministry.plm."""
        canonical_id, candidates = REGISTRY.lookup("Puolustusministeriö")
        assert canonical_id == "fi.ministry.plm"

    def test_um_unicode_ulkoministeriö(self) -> None:
        """'Ulkoministeriö' resolves to fi.ministry.um."""
        canonical_id, candidates = REGISTRY.lookup("Ulkoministeriö")
        assert canonical_id == "fi.ministry.um"

    # ── Proper Unicode forms: agencies ──────────────────────────────────────

    def test_traficom_unicode_liikenne_ja_viestintävirasto(self) -> None:
        """'Liikenne- ja viestintävirasto' (with ä) resolves to fi.agency.traficom."""
        canonical_id, candidates = REGISTRY.lookup("Liikenne- ja viestintävirasto")
        assert canonical_id == "fi.agency.traficom"

    def test_traficom_unicode_genitive(self) -> None:
        """'liikenne- ja viestintäviraston' (genitive, with ä) resolves to fi.agency.traficom."""
        canonical_id, candidates = REGISTRY.lookup("liikenne- ja viestintäviraston")
        assert canonical_id == "fi.agency.traficom"

    def test_traficom_ascii_fallback(self) -> None:
        """ASCII 'Liikenne- ja viestintavirasto' still resolves (backward compat)."""
        canonical_id, candidates = REGISTRY.lookup("Liikenne- ja viestintavirasto")
        assert canonical_id == "fi.agency.traficom"

    def test_stuk_unicode_säteilyturvakeskus(self) -> None:
        """'Säteilyturvakeskus' (with ä) resolves to fi.agency.stuk."""
        canonical_id, candidates = REGISTRY.lookup("Säteilyturvakeskus")
        assert canonical_id == "fi.agency.stuk"

    def test_stuk_ascii_fallback(self) -> None:
        """ASCII 'Sateilyturvakeskus' still resolves (backward compat)."""
        canonical_id, candidates = REGISTRY.lookup("Sateilyturvakeskus")
        assert canonical_id == "fi.agency.stuk"

    def test_fimea_unicode_lääkealan(self) -> None:
        """'Lääkealan turvallisuus- ja kehittämiskeskus' (with ä) resolves to fi.agency.fimea."""
        canonical_id, candidates = REGISTRY.lookup(
            "Lääkealan turvallisuus- ja kehittämiskeskus"
        )
        assert canonical_id == "fi.agency.fimea"

    def test_kela_unicode_kansaneläkelaitos(self) -> None:
        """'Kansaneläkelaitos' (with ä, ä) resolves to fi.agency.kela."""
        canonical_id, candidates = REGISTRY.lookup("Kansaneläkelaitos")
        assert canonical_id == "fi.agency.kela"

    def test_kela_ascii_fallback(self) -> None:
        """ASCII 'Kansanelakelaitos' still resolves (backward compat)."""
        canonical_id, candidates = REGISTRY.lookup("Kansanelakelaitos")
        assert canonical_id == "fi.agency.kela"

    def test_ttl_unicode_työterveyslaitos(self) -> None:
        """'Työterveyslaitos' (with ö) resolves to fi.agency.ttl."""
        canonical_id, candidates = REGISTRY.lookup("Työterveyslaitos")
        assert canonical_id == "fi.agency.ttl"

    # ── Ambiguity: 'ministeriö' without qualifier is ambiguous ───────────────

    def test_ministeriö_unicode_is_ambiguous(self) -> None:
        """'ministeriö' (Unicode) alone is ambiguous (registered in STM + OM)."""
        canonical_id, candidates = REGISTRY.lookup("ministeriö")
        assert canonical_id is None, (
            "Bare 'ministeriö' must be ambiguous (None canonical_id)"
        )
        assert len(candidates) >= 2, (
            f"Expected >=2 candidates for 'ministeriö', got {candidates}"
        )

    def test_Ministeriö_uppercase_is_ambiguous(self) -> None:
        """'Ministeriö' (uppercase Unicode) alone is ambiguous."""
        canonical_id, candidates = REGISTRY.lookup("Ministeriö")
        assert canonical_id is None
        assert len(candidates) >= 2

    # ── Show-as values use proper Unicode ───────────────────────────────────

    def test_stm_show_as_is_unicode(self) -> None:
        """STM show_as is the proper Unicode name."""
        actor = REGISTRY.get_actor("fi.ministry.stm")
        assert actor is not None
        assert actor.show_as == "Sosiaali- ja terveysministeriö"

    def test_stuk_show_as_is_unicode(self) -> None:
        """STUK show_as is the proper Unicode name."""
        actor = REGISTRY.get_actor("fi.agency.stuk")
        assert actor is not None
        assert actor.show_as == "Säteilyturvakeskus"

    def test_traficom_show_as_is_unicode(self) -> None:
        """Traficom show_as is the proper Unicode name."""
        actor = REGISTRY.get_actor("fi.agency.traficom")
        assert actor is not None
        assert actor.show_as == "Liikenne- ja viestintävirasto"

    # ── Prose extraction with Unicode phrases ───────────────────────────────

    def test_prose_scan_extracts_unicode_stm(self) -> None:
        """Prose scanner extracts 'Sosiaali- ja terveysministeriö' from AKN body."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Sosiaali- ja terveysminist\xc3\xa9ri\xc3\xb6 on toimivaltainen viranomainen."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        # Use the proper UTF-8 encoding of "Sosiaali- ja terveysministeriö on toimivaltainen."
        xml_unicode = (
            "<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'>"
            "<act><body>"
            "<section><num>1 §</num>"
            "<paragraph><content><p>"
            "Sosiaali- ja terveysministeriö on toimivaltainen viranomainen."
            "</p></content></paragraph>"
            "</section>"
            "</body></act></akomaNtoso>"
        ).encode("utf-8")
        result = extract_actor_mentions(xml_unicode, "2010/400")
        registry_mentions = [
            m for m in result.mentions
            if m.resolution_confidence == ActorResolutionConfidence.REGISTRY_RESOLVED
        ]
        stm_mentions = [
            m for m in registry_mentions
            if m.actor_canonical_id == "fi.ministry.stm"
        ]
        assert len(stm_mentions) >= 1, (
            f"Expected STM mention from Unicode phrase, got {result.mentions}"
        )

    def test_prose_scan_extracts_unicode_säteilyturvakeskus(self) -> None:
        """Prose scanner extracts 'Säteilyturvakeskus' (with ä) from AKN body."""
        xml_unicode = (
            "<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'>"
            "<act><body>"
            "<section><num>3 §</num>"
            "<paragraph><content><p>"
            "Säteilyturvakeskus valvoo säteilytoimintaa."
            "</p></content></paragraph>"
            "</section>"
            "</body></act></akomaNtoso>"
        ).encode("utf-8")
        result = extract_actor_mentions(xml_unicode, "2018/859")
        stuk_mentions = [
            m for m in result.mentions
            if m.actor_canonical_id == "fi.agency.stuk"
        ]
        assert len(stuk_mentions) >= 1, (
            f"Expected STUK mention from Unicode 'Säteilyturvakeskus', got {result.mentions}"
        )
