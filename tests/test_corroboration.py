"""Hermetic tests for the jurisdiction-neutral CORROBORATE edge.

Prove the routing state machine's S4' (corroborate-before-✔) OFFLINE with injected
stub readers — NO localhost backend, no farchive, no vision model. The cheap lane
earns a CANDIDATE; an injected vision witness resolves it into a RECEIPT that RECORDS
(agreed / verdict_changed) without itself asserting a graduation.
"""
from __future__ import annotations

from lawvm.ingest.corroboration import (
    CorroborationReceipt,
    EscalationKind,
    EscalationPending,
    corroborate,
    witness_fingerprint,
)

# A deterministic garbled candidate (consonant sludge: vowel-degenerate + low-bigram, so
# ``lexical_implausibility`` fires) vs a clean, materially-different, MORE-plausible read.
_GARBLED_CANDIDATE = "bcdfghjklmnpqrstvwxzbcdfghjklmn"
_CLEAN_VISION = "the section is amended as follows in this act"
_CLEAN_CANDIDATE = "the section is amended as follows"


def _pending(kind: EscalationKind = EscalationKind.GARBLE_READ, *, candidate: str) -> EscalationPending:
    return EscalationPending(
        unit_id="HE 1/2020 vp",
        kind=kind,
        reason="garbled text layer (test)",
        region="akn/fi/.../main.pdf#p1",
        candidate_text=candidate,
    )


def test_agree_stub_yields_corroborated_not_verdict_changed() -> None:
    """(a) A witness returning text MATCHING the candidate → agreed, not verdict_changed."""
    pending = _pending(candidate=_CLEAN_CANDIDATE)
    receipt = corroborate(
        pending,
        vision_reader=lambda p: p.candidate_text or "",  # AGREE: echoes the candidate
        witness_prompt="transcribe this region verbatim",
        witness_model="vision-model-v1",
    )
    assert receipt is not None
    assert receipt.agreed is True
    assert receipt.verdict_changed is False
    assert receipt.candidate == _CLEAN_CANDIDATE
    assert receipt.vision_read == _CLEAN_CANDIDATE
    assert receipt.region == "akn/fi/.../main.pdf#p1"


def test_disagree_stub_flags_verdict_changed_and_does_not_certify() -> None:
    """(b) A materially different, MORE-plausible witness read → verdict_changed (candidate WRONG).

    The receipt is NOT a certification: ``verdict_changed`` + not ``agreed`` means the
    deterministic candidate would have been a FALSE exact — the caller must supersede it,
    never emit it as verified.
    """
    pending = _pending(candidate=_GARBLED_CANDIDATE)
    receipt = corroborate(
        pending,
        vision_reader=lambda p: _CLEAN_VISION,  # DISAGREE: a different, more-plausible read
        witness_prompt="transcribe this region verbatim",
        witness_model="vision-model-v1",
    )
    assert receipt is not None
    assert receipt.agreed is False
    assert receipt.verdict_changed is True
    # The receipt RECORDS a caught false-exact; it asserts no graduation on its own.
    assert receipt.vision_read == _CLEAN_VISION
    assert receipt.candidate == _GARBLED_CANDIDATE


def test_offline_reader_none_stays_escalation_pending() -> None:
    """(c) No witness (the free offline sweep) → ``None``; the unit stays escalation-pending."""
    pending = _pending(candidate=_GARBLED_CANDIDATE)
    assert corroborate(pending, vision_reader=None) is None


def test_witness_silence_is_not_corroboration() -> None:
    """A witness that could not read the region (empty read) → ``None``, never a fabricated ✔."""
    pending = _pending(candidate=_CLEAN_CANDIDATE)
    assert corroborate(pending, vision_reader=lambda p: "") is None
    assert corroborate(pending, vision_reader=lambda p: "   \n  ") is None


def test_receipt_fingerprint_is_deterministic_and_model_prompt_sensitive() -> None:
    """The witness fingerprint is stable across runs and re-keys on a prompt/model swap."""
    pending = _pending(candidate=_CLEAN_CANDIDATE)
    reader = lambda p: p.candidate_text or ""  # noqa: E731

    r1 = corroborate(pending, vision_reader=reader, witness_prompt="P", witness_model="M")
    r2 = corroborate(pending, vision_reader=reader, witness_prompt="P", witness_model="M")
    assert r1 is not None and r2 is not None
    # DETERMINISTIC: identical (prompt, model) → identical fingerprint.
    assert r1.witness_fingerprint == r2.witness_fingerprint
    assert r1.witness_fingerprint == witness_fingerprint(witness_prompt="P", witness_model="M")

    # SENSITIVE: a model swap or a prompt edit changes the fingerprint.
    r_model = corroborate(pending, vision_reader=reader, witness_prompt="P", witness_model="M2")
    r_prompt = corroborate(pending, vision_reader=reader, witness_prompt="P2", witness_model="M")
    assert r_model is not None and r_prompt is not None
    assert r_model.witness_fingerprint != r1.witness_fingerprint
    assert r_prompt.witness_fingerprint != r1.witness_fingerprint


def test_escalation_kind_is_a_closed_str_enum() -> None:
    """``EscalationKind`` is a closed StrEnum with serialization-stable lowercase-slug values."""
    import re

    for member in EscalationKind:
        assert isinstance(member, str)
        assert re.fullmatch(r"[a-z][a-z_]*", member.value), member.value
    values = [m.value for m in EscalationKind]
    assert len(values) == len(set(values))  # no collapsed duplicates


def test_every_escalation_kind_has_a_corroborate_path() -> None:
    """TOTALITY: every closed ``EscalationKind`` resolves through ``corroborate``.

    Mirrors the fold/recovery-kind inventory idiom — a newly-added escalation reason can
    never silently lack a resolution edge. For EVERY kind an injected witness yields a
    receipt carrying that kind (the corroborate edge is total over the closed vocabulary).
    """
    for kind in EscalationKind:
        pending = _pending(kind, candidate=_CLEAN_CANDIDATE)
        receipt = corroborate(pending, vision_reader=lambda p: p.candidate_text or "")
        assert isinstance(receipt, CorroborationReceipt), kind
        assert receipt.kind is kind


def test_fi_escalation_status_map_covers_pending_statuses_one_to_one() -> None:
    """The FI status→kind map is 1:1 with the FI escalation-pending status tuple.

    Keeps the jurisdiction-neutral kind vocabulary bound to the FI-local escalation
    statuses: a new escalation status must register its kind (no unmapped escalation).
    """
    from lawvm.tools.fi_he_ir_compare import (
        _ESCALATION_PENDING_STATUSES,
        _ESCALATION_STATUS_TO_KIND,
    )

    assert set(_ESCALATION_STATUS_TO_KIND) == set(_ESCALATION_PENDING_STATUSES)
    for kind in _ESCALATION_STATUS_TO_KIND.values():
        assert isinstance(kind, EscalationKind)
