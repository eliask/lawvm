"""Tests for the H4 surface actor/modal frame recognizer.

The recognizer records SURFACE FACTS ONLY. These tests assert the surface
shapes (token / polarity / voice / spans / status) and the typed-residual
behaviour, and explicitly assert that NO legal conclusion vocabulary is ever
produced.
"""
from __future__ import annotations

from dataclasses import fields

from lawvm.finland.references.actor_modal import (
    ActorModalFrame,
    ActorModalResidual,
    SurfaceModality,
    recognize_actor_modal_frames,
)


def _frame_for_actor(scan, actor_fragment: str) -> ActorModalFrame:
    for frame in scan.frames:
        if actor_fragment in frame.actor_surface:
            return frame
    raise AssertionError(
        f"no frame for actor {actor_fragment!r}; got "
        f"{[f.actor_surface for f in scan.frames]}"
    )


def test_active_positive_frame() -> None:
    text = "Valtioneuvosto voi antaa tarkempia säännöksiä."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Valtioneuvosto")
    assert frame.modal.token == "voi"
    assert frame.modal.polarity == "positive"
    assert frame.modal.voice == "active"
    assert frame.actor_status == "surface_fact_only"
    assert frame.actor_surface == "Valtioneuvosto"


def test_negated_polarity_ei_saa() -> None:
    text = "Viranomainen ei saa luovuttaa tietoja."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Viranomainen")
    assert frame.modal.token == "ei saa"
    assert frame.modal.polarity == "negative"
    assert frame.modal.voice == "active"


def test_passive_voice_saadetaan() -> None:
    text = "Valtioneuvoston asetuksella säädetään tarkemmin maksuista."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Valtioneuvoston")
    assert frame.modal.token == "säädetään"
    assert frame.modal.voice == "passive"
    assert frame.modal.polarity == "positive"


def test_on_velvollinen_longest_first_beats_on() -> None:
    text = "Työnantaja on velvollinen ilmoittamaan muutoksista."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Työnantaja")
    # Must match the longer "on velvollinen", not the bare "on".
    assert frame.modal.token == "on velvollinen"


def test_on_oikeus_role_actor() -> None:
    text = "Hakija on oikeus saada päätös perusteluineen."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Hakija")
    assert frame.modal.token == "on oikeus"
    assert frame.modal.voice == "active"


# ---------------------------------------------------------------------------
# Bare "on" fires ONLY in the necessive construction (on + -ttava/-tava/-tävä).
# A plain copula ("X on Y") or a relative-clause copula ("joka on tehty ...")
# is NOT a deontic modal and must not produce a frame.
# ---------------------------------------------------------------------------


def test_on_necessive_participle_is_a_frame() -> None:
    text = "Viranomaisen on toimitettava asiakirja hakijalle."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Viranomaisen")
    assert frame.modal.token == "on"


def test_bare_on_copula_is_not_a_modal_frame() -> None:
    # "Viranomainen on toimivaltainen" is a plain copula, not a deontic modal.
    text = "Viranomainen on toimivaltainen tässä asiassa."
    scan = recognize_actor_modal_frames(text)
    assert all(f.modal.token != "on" for f in scan.frames)


def test_relative_clause_on_copula_is_not_a_modal_frame() -> None:
    # "joka on tehty ... esittelystä" is a relative-clause copula, not a modal.
    text = "Päätös, jonka viranomainen on tehnyt esittelystä, annetaan tiedoksi."
    scan = recognize_actor_modal_frames(text)
    assert all(f.modal.token != "on" for f in scan.frames)


def test_object_span_captured() -> None:
    text = "Kunta päättää palveluiden järjestämisestä."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Kunta")
    assert frame.object_span is not None
    obj = text[
        frame.object_span.byte_offset : frame.object_span.byte_offset
        + frame.object_span.byte_len
    ]
    assert "palveluiden järjestämisestä" in obj


def test_spans_round_trip_to_source() -> None:
    text = "Tuomioistuin voi pidentää määräaikaa."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Tuomioistuin")
    a = frame.actor_span
    assert text[a.byte_offset : a.byte_offset + a.byte_len] == "Tuomioistuin"
    m = frame.modal.source_span
    assert text[m.byte_offset : m.byte_offset + m.byte_len] == "voi"


def test_modal_without_actor_is_typed_residual() -> None:
    text = "Tällöin säädetään asetuksella tarkemmin."
    scan = recognize_actor_modal_frames(text)
    assert scan.frames == ()
    kinds = {r.kind for r in scan.residuals}
    assert "modal_without_actor" in kinds
    res = next(r for r in scan.residuals if r.kind == "modal_without_actor")
    # Self-evidencing: detail embeds the offending surface text.
    assert "säädetään" in res.detail
    assert res.surface_text == "säädetään"


def test_ambiguous_actor_is_typed_residual_not_silent() -> None:
    # Bare "ministeriö" is registered as ambiguous across multiple ministries.
    text = "Ministeriö antaa tarkemmat määräykset."
    scan = recognize_actor_modal_frames(text)
    amb = [r for r in scan.residuals if r.kind == "ambiguous_actor"]
    assert amb, "ambiguous bare ministeriö must surface as a residual"
    assert "Ministeriö" in amb[0].surface_text or "ministeriö" in amb[0].surface_text
    # Must NOT have silently picked a single ministry into a frame.
    assert all("inisteriö" not in f.actor_surface for f in scan.frames)


def test_no_legal_conclusion_vocabulary_ever() -> None:
    text = (
        "Valtioneuvosto voi antaa asetuksen. Viranomainen ei saa kieltäytyä. "
        "Työnantaja on velvollinen ilmoittamaan. Hakija on oikeus tietoon."
    )
    scan = recognize_actor_modal_frames(text)
    banned = {"duty", "discretion", "power", "obligation", "right_to", "velvollisuus"}
    for frame in scan.frames:
        assert frame.actor_status == "surface_fact_only"
        assert frame.modal.token not in banned
        # No field anywhere carries an interpretive conclusion string.
        for f in fields(frame):
            val = getattr(frame, f.name)
            assert val not in banned


def test_frozen_types() -> None:
    text = "Kunta voi periä maksun."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Kunta")
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.actor_surface = "x"  # ty: ignore[invalid-assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.modal.token = "y"  # ty: ignore[invalid-assignment]


def test_source_file_propagated_to_spans() -> None:
    text = "Kunta päättää asiasta."
    scan = recognize_actor_modal_frames(text, source_file="711/2022")
    frame = _frame_for_actor(scan, "Kunta")
    assert frame.source_span.source_file == "711/2022"
    assert frame.actor_span.source_file == "711/2022"
    assert frame.modal.source_span.source_file == "711/2022"


def test_residuals_are_typed_dataclass() -> None:
    text = "Tällöin annetaan tarkemmat ohjeet."
    scan = recognize_actor_modal_frames(text)
    assert all(isinstance(r, ActorModalResidual) for r in scan.residuals)


def test_surface_modality_construction() -> None:
    from lawvm.core.reference_mention import SourceSpan

    mod = SurfaceModality(
        token="voidaan",
        polarity="positive",
        voice="passive",
        source_span=SourceSpan(source_file="", byte_offset=0, byte_len=7),
    )
    assert mod.voice == "passive"


# ---------------------------------------------------------------------------
# Hand-sample coverage tally
# ---------------------------------------------------------------------------

# A small hand-curated sample of statutory clauses, each known to carry exactly
# one surface actor/modal frame. The tally asserts the recognizer covers them.
_HAND_SAMPLE: tuple[tuple[str, str | None, str | None], ...] = (
    # (clause, expected actor fragment, expected modal token)
    ("Valtioneuvosto voi antaa asetuksia.", "Valtioneuvosto", "voi"),
    ("Viranomainen ei saa luovuttaa salassa pidettäviä tietoja.", "Viranomainen", "ei saa"),
    ("Kunta päättää palvelujen järjestämisestä.", "Kunta", "päättää"),
    ("Työnantaja on velvollinen järjestämään työterveyshuollon.", "Työnantaja", "on velvollinen"),
    ("Asetuksella säädetään tarkemmin maksujen perusteista.", None, None),  # no actor -> residual
    ("Hakija saa hakea muutosta valittamalla.", "Hakija", "saa"),
    ("Tuomioistuin voi pidentää määräaikaa erityisestä syystä.", "Tuomioistuin", "voi"),
    ("Verohallinto antaa tarkemmat määräykset ilmoittamisesta.", "Verohallinto", "antaa"),
)


def test_hand_sample_coverage_tally() -> None:
    covered = 0
    residual_only = 0
    for clause, exp_actor, exp_modal in _HAND_SAMPLE:
        scan = recognize_actor_modal_frames(clause)
        if exp_actor is None:
            # Expect a typed residual, not a frame.
            assert any(
                r.kind == "modal_without_actor" for r in scan.residuals
            ), f"expected modal_without_actor residual for: {clause!r}"
            residual_only += 1
            continue
        matched = [
            f
            for f in scan.frames
            if exp_actor in f.actor_surface and f.modal.token == exp_modal
        ]
        assert matched, (
            f"clause not covered: {clause!r}; frames="
            f"{[(f.actor_surface, f.modal.token) for f in scan.frames]}"
        )
        covered += 1

    frame_clauses = sum(1 for _, a, _ in _HAND_SAMPLE if a is not None)
    coverage = covered / frame_clauses
    # All frame-bearing hand-sample clauses must be covered.
    assert coverage == 1.0, f"coverage {coverage:.2%} ({covered}/{frame_clauses})"
    assert residual_only == 1


def test_actor_modal_not_fused_across_sentence_boundary() -> None:
    """An actor at the tail of sentence N must NOT bind a modal in sentence N+1.

    Without a same-sentence guard the small char-gap fuses two sentences into one
    fabricated frame (the coverage triage's cross-sentence false positive). With
    the guard the fabrication is gone, and the second sentence's modal — which has
    no actor in its OWN sentence — becomes a typed ``modal_without_actor`` residual
    (fail-loud), never a guessed cross-sentence frame.
    """
    text = (
        "Valtioneuvosto asettaa kuntaselvitykselle määräajan. "
        "Päätös voidaan peruuttaa, jos aihetta ei enää ole."
    )
    scan = recognize_actor_modal_frames(text)
    # No frame may span the sentence boundary: the '.' before "Päätös".
    boundary = text.index(". ") + 1
    for frame in scan.frames:
        span = frame.source_span
        assert not (
            span.byte_offset < boundary < span.byte_offset + span.byte_len
        ), f"frame fuses across the sentence boundary: {frame!r}"
    # The cross-sentence Valtioneuvosto+voidaan fabrication must be gone.
    assert not any(
        f.actor_surface == "Valtioneuvosto" and f.modal.token == "voidaan"
        for f in scan.frames
    )
    # The orphaned modal is a typed residual (fail-loud), never a guessed frame.
    assert any(
        r.kind == "modal_without_actor" and r.surface_text == "voidaan"
        for r in scan.residuals
    )


def test_actor_modal_same_sentence_frame_still_binds() -> None:
    """A genuine actor+modal in ONE sentence is unaffected by the guard."""
    text = "Valtioneuvosto voi antaa tarkemmat säännökset asiasta."
    scan = recognize_actor_modal_frames(text)
    frame = _frame_for_actor(scan, "Valtioneuvosto")
    assert frame.modal.token == "voi"


def test_actor_modal_terminator_abutting_actor_end() -> None:
    """A '.' directly abutting the actor end still separates it from a later modal."""
    text = "Pidättämisestä päättää tuomioistuin. Laillisuus voidaan tutkia."
    scan = recognize_actor_modal_frames(text)
    assert not any(
        f.actor_surface == "tuomioistuin" and f.modal.token == "voidaan"
        for f in scan.frames
    )
