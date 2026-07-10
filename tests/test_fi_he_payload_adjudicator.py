"""Hermetic tests for the HE payload-divergence adjudicator.

The LLM transport is INJECTED (``chat_fn``), so these exercise the FI-specific prompt,
verdict typing and witness-disagreement accounting with a scripted fake — no backend.
"""
from __future__ import annotations

from lawvm.finland.he_payload_adjudicator import (
    DivergenceVerdict,
    adjudicate_payload_divergence,
    build_adjudication_prompt,
    parse_verdict,
)


def test_prompt_snippet_is_bounded_and_centered_on_the_divergence() -> None:
    left = "A" * 2000 + "johdosta" + "Z" * 2000
    right = "A" * 2000 + "jo/ulosta" + "Z" * 2000
    system, user = build_adjudication_prompt(left, right)
    assert "Finnish" in system and "ORACLE_ARTIFACT" in system
    # the user message is trimmed around the first difference, not the whole 4000-char body
    assert len(user) < 2000
    assert "johdosta" in user and "jo/ulosta" in user


def test_parse_verdict_recognizes_each_label() -> None:
    assert parse_verdict("ORACLE_ARTIFACT") is DivergenceVerdict.ORACLE_ARTIFACT
    assert parse_verdict("the answer is READER_DEFECT\n") is DivergenceVerdict.READER_DEFECT
    assert parse_verdict("GENUINE_DIFFERENCE") is DivergenceVerdict.GENUINE_DIFFERENCE
    assert parse_verdict("EQUIVALENT.") is DivergenceVerdict.EQUIVALENT


def test_parse_verdict_unrecognized_is_uncertain_never_raises() -> None:
    assert parse_verdict("I have no idea") is DivergenceVerdict.UNCERTAIN
    assert parse_verdict("") is DivergenceVerdict.UNCERTAIN


def test_witness_disagreement_accounting() -> None:
    # oracle artifact + genuine difference are first-class witness_disagreement (done);
    # a reader defect is a reconstruction defect, NOT witness_disagreement.
    assert DivergenceVerdict.ORACLE_ARTIFACT.is_witness_disagreement
    assert DivergenceVerdict.GENUINE_DIFFERENCE.is_witness_disagreement
    assert not DivergenceVerdict.READER_DEFECT.is_witness_disagreement
    assert not DivergenceVerdict.EQUIVALENT.is_witness_disagreement
    assert not DivergenceVerdict.UNCERTAIN.is_witness_disagreement


def test_adjudicate_routes_through_injected_chat() -> None:
    # the XML has a run-together "verovirastontai" the PDF splits correctly → ORACLE_ARTIFACT
    captured: dict[str, str] = {}

    def fake_chat(system: str, user: str) -> str:
        captured["user"] = user
        return "ORACLE_ARTIFACT"

    v = adjudicate_payload_divergence(
        "hallituksen, verovirastontai veroasiamiehen",
        "hallituksen, veroviraston tai veroasiamiehen",
        chat_fn=fake_chat,
    )
    assert v is DivergenceVerdict.ORACLE_ARTIFACT
    assert v.is_witness_disagreement
    assert "verovirastontai" in captured["user"]
