"""Tests for the centralised UK affecting-class slug derivation.

Covers the shared ``affecting_class_slug`` helper, the ``UnmappedAffectingClass``
typed diagnostic, the ``is_affecting_class_recognized`` predicate, and the
production-lane behaviour of both call sites that previously inlined the
silent ``cls.lower()`` fallback (AGENTS.md §1.10 literal DON'T example):

- ``UKEffectRecord.affecting_act_id`` — site 1: the property raises
  ``UnmappedAffectingClass`` so the producing-act id cannot silently become
  an invalid slug (``northernirelandact/2016/10``) that 404s at archive fetch.
- ``tools.uk_cross_statute_graph._affected_statute_id`` — site 2: the
  affected-statute id falls through to the queried statute id (a self-edge)
  AND emits a typed ``uk_affected_act_class_unmapped_rejected`` finding so
  the residual is owned rather than silently produced as a bad target.

Both pre-existing sites used the identical ``_UK_AFFECTING_CLASS_SLUG_MAP.get(
cls, cls.lower())`` shape — the missing abstraction called out in AGENTS.md
§2.6 (rule of three) — crystallised here into the shared helper.
"""
from __future__ import annotations

import pytest

from lawvm.uk_legislation.affecting_class import (
    UnmappedAffectingClass,
    affecting_class_slug,
    is_affecting_class_recognized,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.tools.uk_cross_statute_graph import (
    CrossStatuteEdge,
    _affected_statute_id,
    edge_from_effect,
)


#─ Helper unit tests──────────────────────────────────────────────────────────


class TestAffectingClassSlug:
    def test_mapped_class_returns_slug(self) -> None:
        assert (
            affecting_class_slug("UnitedKingdomPublicGeneralAct", year="2023", number="28")
            == "ukpga"
        )
        assert (
            affecting_class_slug("ScottishAct", year="2000", number="6") == "asp"
        )
        assert (
            affecting_class_slug("NorthernIrelandAssemblyMeasure", year="2011", number="2")
            == "mnia"
        )

    def test_unmapped_class_raises_with_fields(self) -> None:
        # ``NorthernIrelandAct`` is the AGENTS.md §1.10 motivating example:
        # ``cls.lower()`` would produce the invalid ``northernirelandact`` slug
        # (a 404) when the correct slug is ``nia``.
        with pytest.raises(UnmappedAffectingClass) as excinfo:
            affecting_class_slug("NorthernIrelandAct", year="2016", number="10")
        exc = excinfo.value
        assert exc.cls == "NorthernIrelandAct"
        assert exc.year == "2016"
        assert exc.number == "10"
        assert "add an entry to _UK_AFFECTING_CLASS_SLUG_MAP" in exc.hint
        assert "AffectingURI" in exc.hint

    def test_unmapped_class_with_missing_year_number_still_raises(self) -> None:
        # The fields are optional (for diagnostics context), but the raise
        # must fire regardless — a missing year/number does not promote an
        # unmapped class to "guessed-fallback" status.
        with pytest.raises(UnmappedAffectingClass) as excinfo:
            affecting_class_slug("SomeUnknownClass", year=None, number=None)
        assert excinfo.value.cls == "SomeUnknownClass"
        assert excinfo.value.year is None
        assert excinfo.value.number is None

    def test_empty_string_class_raises(self) -> None:
        # An empty class string is a residual (source feed had no class data),
        # not a "fall back to ''.lower()" silent slug. The raise is loud and
        # carries the offending (empty) value, so a downstream filter can
        # distinguish it from "absent class with usable URI".
        with pytest.raises(UnmappedAffectingClass) as excinfo:
            affecting_class_slug("", year="2020", number="1")
        assert excinfo.value.cls == ""

    def test_exception_message_embeds_fields(self) -> None:
        # AGENTS.md §1.10: "A diagnostic about source text the pipeline could
        # not handle MUST embed the offending clause/snippet" — equivalent
        # here for an offending class string. The str(exc) must carry the
        # class, year, number, and hint so log readers can triage without
        # re-running extraction.
        with pytest.raises(UnmappedAffectingClass) as excinfo:
            affecting_class_slug("NorthernIrelandAct", year="2016", number="10")
        message = str(excinfo.value)
        assert "NorthernIrelandAct" in message
        assert "2016" in message
        assert "10" in message
        assert "_UK_AFFECTING_CLASS_SLUG_MAP" in message


class TestIsAffectingClassRecognized:
    def test_uri_makes_unmapped_class_recognized(self) -> None:
        # NorthernIrelandAct is NOT in the slug map, but a usable AffectingURI
        # makes it recognized: the URI carries the authoritative slug.
        assert (
            is_affecting_class_recognized(
                cls="NorthernIrelandAct",
                uri="http://www.legislation.gov.uk/id/nia/2016/10",
            )
            is True
        )

    def test_mapped_class_without_uri_recognized(self) -> None:
        assert (
            is_affecting_class_recognized(cls="ScottishAct", uri="") is True
        )

    def test_unmapped_class_without_uri_not_recognized(self) -> None:
        assert (
            is_affecting_class_recognized(cls="NorthernIrelandAct", uri="") is False
        )

    def test_empty_class_and_uri_not_recognized(self) -> None:
        assert is_affecting_class_recognized(cls="", uri="") is False

    def test_uri_without_legislation_prefix_not_recognized(self) -> None:
        # The URI regex requires ``legislation.gov.uk/`` to anchor; a bare
        # ``/id/ukpga/2023/28`` (no host) is NOT sufficient. This is a
        # recognition test for the predicate, not a slug lookup.
        assert (
            is_affecting_class_recognized(
                cls="NorthernIrelandOrderInCouncil",
                uri="/id/nisi/2007/1234",
            )
            is False
        )


#─ Production-lane tests (driving real call sites)───────────────────────────
# AGENTS.md §2.9 guard-liveness preference: drive through the production path.


def _effect_record(
    *,
    affecting_class: str = "UnitedKingdomPublicGeneralAct",
    affecting_uri: str = "",
    affecting_year: str = "2024",
    affecting_number: str = "13",
    affected_class: str = "UnitedKingdomPublicGeneralAct",
    affected_uri: str = "",
    affected_year: str = "2000",
    affected_number: str = "10",
    effect_id: str = "e1",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri=affected_uri,
        affected_class=affected_class,
        affected_year=affected_year,
        affected_number=affected_number,
        affected_provisions="s. 1",
        affecting_uri=affecting_uri,
        affecting_class=affecting_class,
        affecting_year=affecting_year,
        affecting_number=affecting_number,
        affecting_provisions="Sch. 1",
        affecting_title="Test Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )


class TestAffectingActIdProperty:
    """Site 1 — ``UKEffectRecord.affecting_act_id`` raises on unmapped class."""

    def test_mapped_class_without_uri_returns_slug(self) -> None:
        rec = _effect_record(
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_uri="",
            affecting_year="1968",
            affecting_number="60",
        )
        assert rec.affecting_act_id == "ukpga/1968/60"

    def test_uri_preference_for_unmapped_class(self) -> None:
        rec = _effect_record(
            affecting_class="NorthernIrelandAct",
            affecting_uri="http://www.legislation.gov.uk/id/nia/2016/10",
            affecting_year="2016",
            affecting_number="10",
        )
        assert rec.affecting_act_id == "nia/2016/10"

    def test_unmapped_class_without_uri_raises(self) -> None:
        rec = _effect_record(
            affecting_class="NorthernIrelandAct",
            affecting_uri="",
            affecting_year="2016",
            affecting_number="10",
        )
        assert rec.affecting_class_is_recognized is False
        with pytest.raises(UnmappedAffectingClass) as excinfo:
            _ = rec.affecting_act_id
        assert excinfo.value.cls == "NorthernIrelandAct"
        assert excinfo.value.year == "2016"
        assert excinfo.value.number == "10"

    def test_predicate_gates_safe_access(self) -> None:
        # A caller that pre-checks ``affecting_class_is_recognized`` and routes
        # the unmapped case to a typed finding (the recommended pattern) never
        # crashes; accessing the property only when the predicate is True is
        # safe.
        rec = _effect_record(affecting_class="NorthernIrelandAct", affecting_uri="")
        if rec.affecting_class_is_recognized:
            _ = rec.affecting_act_id  # not reached in this test


class TestAffectedStatuteIdSite2:
    """Site 2 — ``_affected_statute_id`` falls through + emits a finding."""

    def test_mapped_affected_class_returns_slug(self) -> None:
        rec = _effect_record(
            affected_class="ScottishAct",
            affected_uri="",
            affected_year="2000",
            affected_number="6",
        )
        assert (
            _affected_statute_id(rec, fallback="fallback/used/here")
            == "asp/2000/6"
        )

    def test_affected_uri_preference(self) -> None:
        rec = _effect_record(
            affected_class="NorthernIrelandAct",
            affected_uri="http://www.legislation.gov.uk/id/nia/2016/10",
            affected_year="2016",
            affected_number="10",
        )
        assert _affected_statute_id(rec, fallback="fallback") == "nia/2016/10"

    def test_unmapped_affected_class_falls_through_and_emits_finding(self) -> None:
        # The fallback (queried statute id) is used as edge target, and a
        # typed finding is emitted via the diagnostics channel so the residual
        # is owned (AGENTS.md §0/§1.10).
        rec = _effect_record(
            affected_class="NorthernIrelandAct",
            affected_uri="",
            affected_year="2016",
            affected_number="10",
            effect_id="e_affected",
        )
        diagnostics: list[dict] = []
        result = _affected_statute_id(
            rec,
            fallback="ukpga/2000/10",
            unmapped_diagnostics_out=diagnostics,
        )
        assert result == "ukpga/2000/10"  # fallback used as the target
        assert len(diagnostics) == 1
        finding = diagnostics[0]
        assert finding["rule_id"] == "uk_affected_act_class_unmapped_rejected"
        assert finding["family"] == "source_pathology"
        assert finding["phase"] == "acquisition"
        # Self-evidencing fields per AGENTS.md §1.10 (flattened into the top-level
        # payload by ``diagnostic_detail``):
        assert finding["affected_class"] == "NorthernIrelandAct"
        assert finding["affected_year"] == "2016"
        assert finding["affected_number"] == "10"
        assert finding["fallback_statute_id"] == "ukpga/2000/10"
        assert "add an entry to _UK_AFFECTING_CLASS_SLUG_MAP" in finding["hint"]
        assert finding["effect_id"] == "e_affected"

    def test_unmapped_affected_class_with_no_diagnostics_channel_silent_fallback(
        self,
    ) -> None:
        # When no diagnostic channel is provided (None), the fallback is
        # returned silently rather than crashing — the cross-statute-graph
        # is read-only and never crashes on a single unmapped record.
        rec = _effect_record(
            affected_class="NorthernIrelandAct",
            affected_uri="",
            affected_year="2016",
            affected_number="10",
        )
        assert _affected_statute_id(rec, fallback="qstatute/2000/10") == "qstatute/2000/10"

    def test_missing_year_number_for_unmapped_class_uses_fallback(self) -> None:
        # Year/number absent → the existing pre-helper behavior falls through
        # to fallback. The unmapped-class raise doesn't even fire because the
        # branch is gated by year/number/cls all being present.
        rec = _effect_record(
            affected_class="UnknownClass",
            affected_uri="",
            affected_year="",
            affected_number="",
        )
        diagnostics: list[dict] = []
        assert _affected_statute_id(
            rec,
            fallback="f",
            unmapped_diagnostics_out=diagnostics,
        ) == "f"
        assert diagnostics == []


class TestEdgeFromEffectWithUnmappedAffectedClass:
    """The edge is still built (target falls through) and the finding flows up."""

    def test_edge_built_with_fallback_target_and_unmapped_finding(self) -> None:
        rec = _effect_record(
            affected_class="NorthernIrelandAct",
            affected_uri="",
            affected_year="2016",
            affected_number="10",
            effect_id="e1",
        )
        diagnostics: list[dict] = []
        edge = edge_from_effect(
            rec,
            affected_statute_id="ukpga/2000/10",
            base_statute_ids=None,
            unmapped_diagnostics_out=diagnostics,
        )
        assert isinstance(edge, CrossStatuteEdge)
        assert edge.target_statute == "ukpga/2000/10"
        assert len(diagnostics) == 1
        assert diagnostics[0]["affected_class"] == "NorthernIrelandAct"


#─ No-leak / negative tests (AGENTS.md §2.9)─────────────────────────────────


class TestNoLeakAndGuards:
    def test_unmapped_class_exception_does_not_leak_into_witness_for_mapped(
        self,
    ) -> None:
        # No-leak: a happy-path mapped class never surfaces an
        # UnmappedAffectingClass exception or finding, even via the helper.
        rec = _effect_record(
            affecting_class="UnitedKingdomStatutoryInstrument",
            affecting_uri="",
            affecting_year="1994",
            affecting_number="1935",
        )
        assert rec.affecting_class_is_recognized is True
        assert rec.affecting_act_id == "uksi/1994/1935"

    def test_negative_known_class_does_not_emit_affected_unmapped_finding(
        self,
    ) -> None:
        # Negative test: the rule does not fire on a nearby valid shape (a
        # fully-mapped affected class). No diagnostics emitted.
        rec = _effect_record(
            affected_class="WelshParliamentAct",
            affected_uri="",
            affected_year="2017",
            affected_number="1",
        )
        diagnostics: list[dict] = []
        result = _affected_statute_id(
            rec,
            fallback="f",
            unmapped_diagnostics_out=diagnostics,
        )
        assert result == "asc/2017/1"
        assert diagnostics == []
