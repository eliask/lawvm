"""Tests for the H5 procedure-frame surface lens (procedure.py).

These exercise each closed process kind, actor-span and deadline-span capture,
the fail-loud typed residual for an untypeable process-shaped token, and the
non-negotiable safety boundary: NO legal conclusion vocabulary is ever emitted.
"""
from __future__ import annotations

import dataclasses

import pytest

from lawvm.finland.references.procedure import (
    _ACTOR_PHRASES_BY_FIRST_WORD,
    _ACTOR_PHRASES_LONGEST_FIRST,
    ProcedureFrame,
    ProcedureResidual,
    ProcessKind,
    recognize_procedure_frames,
    scan_procedure,
)


def _kinds(text: str) -> set[ProcessKind]:
    return {f.process_kind for f in recognize_procedure_frames(text)}


# ---------------------------------------------------------------------------
# Each process kind is recognised from an inflected form.
# ---------------------------------------------------------------------------


def test_hakemus_kind() -> None:
    assert ProcessKind.HAKEMUS in _kinds("Toimitettava hakemuksen liitteet.")


def test_paatos_kind() -> None:
    assert ProcessKind.PAATOS in _kinds("Viranomaisen on tehtävä päätös.")


def test_ilmoitus_kind() -> None:
    assert ProcessKind.ILMOITUS in _kinds("Asiasta on tehtävä ilmoitus.")


def test_lausunto_kind() -> None:
    assert ProcessKind.LAUSUNTO in _kinds("Ministeriö antaa lausunnon asiasta.")


def test_valitus_kind() -> None:
    assert ProcessKind.VALITUS in _kinds("Päätökseen saa hakea muutosta valituksella.")


def test_kuuleminen_kind() -> None:
    assert ProcessKind.KUULEMINEN in _kinds("Asianosaisen kuulemista koskeva sääntö.")


def test_maaraaika_kind_from_noun() -> None:
    assert ProcessKind.MAARAAIKA in _kinds("Määräajan kuluessa on toimittava.")


def test_kertomus_kind() -> None:
    assert ProcessKind.KERTOMUS in _kinds("Hallitus antaa kertomuksen eduskunnalle.")


def test_raportti_kind_maps_to_kertomus() -> None:
    assert ProcessKind.KERTOMUS in _kinds("Laitos toimittaa raportin vuosittain.")


def test_all_eight_kinds_distinct() -> None:
    # Sanity: the closed enum has exactly the eight documented kinds.
    assert {k.name for k in ProcessKind} == {
        "HAKEMUS",
        "PAATOS",
        "ILMOITUS",
        "LAUSUNTO",
        "VALITUS",
        "KUULEMINEN",
        "MAARAAIKA",
        "KERTOMUS",
    }


# ---------------------------------------------------------------------------
# Actor-bearing frame.
# ---------------------------------------------------------------------------


def test_actor_bearing_frame() -> None:
    text = "hakijan on toimitettava hakemus"
    frames = recognize_procedure_frames(text)
    hakemus = [f for f in frames if f.process_kind is ProcessKind.HAKEMUS]
    assert len(hakemus) == 1
    f = hakemus[0]
    assert f.actor_span is not None
    actor = text[
        f.actor_span.byte_offset : f.actor_span.byte_offset + f.actor_span.byte_len
    ]
    assert actor == "hakijan"


def test_frame_without_actor_has_none() -> None:
    frames = recognize_procedure_frames("Hakemus käsitellään.")
    assert any(
        f.process_kind is ProcessKind.HAKEMUS and f.actor_span is None
        for f in frames
    )


def test_actor_phrase_first_word_index_preserves_phrase_order() -> None:
    flattened = [
        phrase
        for phrase in _ACTOR_PHRASES_LONGEST_FIRST
        if phrase
    ]
    from_index = [
        phrase
        for first_word in _ACTOR_PHRASES_BY_FIRST_WORD
        for phrase in _ACTOR_PHRASES_BY_FIRST_WORD[first_word]
    ]

    assert set(from_index) == set(flattened)
    for first_word, phrases in _ACTOR_PHRASES_BY_FIRST_WORD.items():
        assert all(phrase[0] == first_word for phrase in phrases)
        assert list(phrases) == [
            phrase
            for phrase in _ACTOR_PHRASES_LONGEST_FIRST
            if phrase and phrase[0] == first_word
        ]


# ---------------------------------------------------------------------------
# Deadline-bearing frame.
# ---------------------------------------------------------------------------


def test_deadline_bearing_frame() -> None:
    text = "päätös on tehtävä 30 päivän kuluessa"
    frames = recognize_procedure_frames(text)
    kinds = {f.process_kind for f in frames}
    # The decision noun frame AND the deadline cue (as a MAARAAIKA frame).
    assert ProcessKind.PAATOS in kinds
    assert ProcessKind.MAARAAIKA in kinds

    paatos = [f for f in frames if f.process_kind is ProcessKind.PAATOS][0]
    assert paatos.deadline_span is not None
    deadline = text[
        paatos.deadline_span.byte_offset : paatos.deadline_span.byte_offset
        + paatos.deadline_span.byte_len
    ]
    assert deadline == "30 päivän kuluessa"


def test_standalone_deadline_emits_maaraaika() -> None:
    frames = recognize_procedure_frames("Toimitettava 14 päivän kuluttua asiakirjat.")
    maaraaika = [f for f in frames if f.process_kind is ProcessKind.MAARAAIKA]
    assert len(maaraaika) == 1
    assert maaraaika[0].deadline_span is not None


# ---------------------------------------------------------------------------
# Fail-loud: untypeable process-shaped token → typed residual (self-evidencing).
# ---------------------------------------------------------------------------


def test_untypeable_process_shaped_token_becomes_residual() -> None:
    text = "Viranomaiselle on tehtävä anomus asiasta."
    scan = scan_procedure(text)
    # "anomus" is process-shaped but NOT in the closed kind set: no frame, a residual.
    assert scan.frames == ()
    assert len(scan.residuals) == 1
    r = scan.residuals[0]
    assert isinstance(r, ProcedureResidual)
    # Self-evidencing: the offending text is embedded verbatim.
    assert "anomus" in r.surface_text
    assert "anomus" in r.detail


def test_residual_never_guessed_into_frame() -> None:
    scan = scan_procedure("Tehtävä vaatimus toimitetaan.")
    # vaatimus is shaped-but-untyped: it must not appear as any typed frame.
    assert scan.frames == ()
    assert len(scan.residuals) == 1
    assert "vaatimus" in scan.residuals[0].surface_text


# ---------------------------------------------------------------------------
# SAFETY BOUNDARY: no legal-conclusion vocabulary anywhere in the output.
# ---------------------------------------------------------------------------


def test_no_legal_conclusion_vocabulary_in_output() -> None:
    """Surface facts only: the recognizer never emits a validity/duty conclusion.

    We assert that no field of any emitted frame/residual contains a token from
    a forbidden legal-conclusion vocabulary (English or Finnish), across a
    representative corpus that includes actors, deadlines, and untypeable tokens.
    """
    forbidden = (
        # English legal conclusions
        "valid",
        "invalid",
        "void",
        "enforceable",
        "binding",
        "duty",
        "obligation",
        "right",
        "power",
        "unlawful",
        "lawful",
        "liable",
        "liability",
        # Finnish legal conclusions
        "pätevä",
        "pätemätön",
        "velvollisuus",
        "velvoite",
        "oikeudellinen",
        "lainvoimainen",
        "sitova",
        "vastuu",
    )
    corpus = [
        "hakijan on toimitettava hakemus",
        "päätös on tehtävä 30 päivän kuluessa",
        "Ministeriö antaa lausunnon ja kertomuksen.",
        "Asianosaisen kuulemista edeltää ilmoitus.",
        "Viranomaiselle on tehtävä anomus määräajassa.",
        "Päätökseen saa hakea muutosta valituksella.",
    ]
    for text in corpus:
        scan = scan_procedure(text)
        emitted_strings: list[str] = []
        for frame in scan.frames:
            for field in dataclasses.fields(frame):
                val = getattr(frame, field.name)
                emitted_strings.append(str(val))
        for residual in scan.residuals:
            for field in dataclasses.fields(residual):
                val = getattr(residual, field.name)
                emitted_strings.append(str(val))
        blob = " ".join(emitted_strings).lower()
        for bad in forbidden:
            assert bad not in blob, (
                f"legal-conclusion token {bad!r} leaked into output for {text!r}"
            )


def test_status_always_surface_fact_only() -> None:
    scan = scan_procedure("hakijan on toimitettava hakemus 30 päivän kuluessa")
    assert scan.frames  # non-empty
    assert all(f.procedure_status == "surface_fact_only" for f in scan.frames)


def test_frames_are_frozen() -> None:
    frame = recognize_procedure_frames("Hakemus käsitellään.")[0]
    assert isinstance(frame, ProcedureFrame)
    # The dataclass is declared frozen; mutation raises at runtime. We use
    # object.__setattr__ to drive the runtime guard without a static
    # read-only-property complaint from the type checker.
    assert frame.__dataclass_params__.frozen  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.__setattr__("procedure_status", "mutated")


def test_empty_and_nonprocedural_text() -> None:
    assert recognize_procedure_frames("") == []
    assert recognize_procedure_frames("Tämä on tavallinen lause ilman prosesseja.") == []
