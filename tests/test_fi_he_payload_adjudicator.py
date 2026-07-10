"""Hermetic tests for the HE payload-divergence adjudicator.

The LLM transport is INJECTED (``chat_fn``), so these exercise the FI-specific prompt,
verdict typing and witness-disagreement accounting with a scripted fake — no backend.
"""
from __future__ import annotations

from lawvm.finland.he_payload_adjudicator import (
    DivergenceVerdict,
    adjudicate_payload_divergence,
    adjudication_prompt_fingerprint,
    build_adjudication_prompt,
    parse_verdict,
)

#: Fingerprint of the adjudication contract at base commit 0e1caf70f — BEFORE the
#: SEGMENTATION_NOISE label + prompt clause were added. The current fingerprint MUST differ
#: from this so the determinism-firewall cache in he_payload_verdict_store re-keys (any stored
#: verdict from the old five-label classifier is invalidated, never served stale).
_FINGERPRINT_BEFORE_SEGMENTATION_NOISE = "e3c3f73a9362ef70"


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


def test_segmentation_noise_label_parses_and_is_defined_in_prompt() -> None:
    # the new boundary/layout-furniture label round-trips through the parser ...
    assert parse_verdict("SEGMENTATION_NOISE") is DivergenceVerdict.SEGMENTATION_NOISE
    assert (
        parse_verdict("this is boundary junk -> SEGMENTATION_NOISE\n")
        is DivergenceVerdict.SEGMENTATION_NOISE
    )
    # ... and it is a documented, choosable option in the system prompt.
    system, _ = build_adjudication_prompt("x", "y")
    assert "SEGMENTATION_NOISE" in system


def test_segmentation_noise_is_not_witness_disagreement() -> None:
    # a PDF body-boundary defect (over-captured header/signature/dashline) is a reader
    # segmentation defect to fix — it MUST NOT inflate the accounted witness_disagreement
    # bucket the way a mislabelled ORACLE_ARTIFACT would.
    assert not DivergenceVerdict.SEGMENTATION_NOISE.is_witness_disagreement
    assert DivergenceVerdict.ORACLE_ARTIFACT.is_witness_disagreement


def test_segmentation_noise_routes_through_injected_chat() -> None:
    # B over-captured a running-header/page-number ("HE 58/1995 vp 12") the XML never had;
    # the overlapping body words agree → SEGMENTATION_NOISE, NOT ORACLE_ARTIFACT.
    def fake_chat(system: str, user: str) -> str:
        return "SEGMENTATION_NOISE"

    v = adjudicate_payload_divergence(
        "Verovelvollisen on annettava veroilmoitus.",
        "Verovelvollisen on annettava veroilmoitus. HE 58/1995 vp 12 — — —",
        chat_fn=fake_chat,
    )
    assert v is DivergenceVerdict.SEGMENTATION_NOISE
    assert not v.is_witness_disagreement


def test_adding_segmentation_noise_rekeys_the_verdict_cache() -> None:
    # the store folds adjudication_prompt_fingerprint() into its content-address key, so the
    # fingerprint MUST have moved off the pre-SEGMENTATION_NOISE value (old cached verdicts
    # from the five-label classifier are invalidated, never served stale).
    assert (
        adjudication_prompt_fingerprint() != _FINGERPRINT_BEFORE_SEGMENTATION_NOISE
    )
    # the fingerprint depends on the label vocabulary, so the new member is folded in.
    assert "segmentation_noise" in [v.value for v in DivergenceVerdict]


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
