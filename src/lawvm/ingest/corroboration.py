"""The CORROBORATE edge of the PDF→IR routing state machine — jurisdiction-neutral.

The cheap/deterministic reading lane earns a **candidate**, never a verdict: a
single reader agreeing with itself is NOT independence, so no candidate may graduate
to a ✔ without an INDEPENDENT vision witness confirming it. This module gives that
edge its contract:

  * :class:`EscalationPending` — a first-class TYPED PENDING. The deterministic lane
    could not certify a unit (a garbled read, a payload dispute, an unconfirmed
    post-repair), so instead of silently absolving OR silently guessing it, the unit
    is parked as an ``EscalationPending`` carrying its CANDIDATE and the reason.
  * :class:`CorroborationReceipt` — the record an injected vision witness produces:
    ``(candidate, vision_read, agreed, verdict_changed, region, witness_fingerprint)``.
    It RECORDS the confrontation of the two independent reads; it never itself asserts
    a graduation (the caller decides). The receipt store is the statistics substrate
    from which the operating point is later derived EMPIRICALLY (do not assume it).
  * :func:`corroborate` — the offline-safe, backend-gated function that drives one
    escalation-pending through an injected vision witness and emits its receipt. With
    NO witness (the free offline sweep) it returns ``None`` — the honest un-resolved
    state, leaving the unit escalation-pending.

The MECHANISM is jurisdiction-neutral: it consumes ONLY the shared canonical
primitives — :func:`~lawvm.ingest.suspect_region.cross_reader_disagrees` and
:func:`~lawvm.ingest.suspect_region.more_plausible` for the agree/verdict-change
judgement (NOT a hand-rolled comparison), and
:func:`~lawvm.ingest.llm_backends.prompt_fingerprint.prompt_fingerprint` for the
witness fingerprint (ties the receipt to the determinism firewall). Any jurisdiction
harness supplies only its own ``vision_reader`` and builds its own pendings.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, Optional

from lawvm.ingest.llm_backends.prompt_fingerprint import prompt_fingerprint
from lawvm.ingest.suspect_region import cross_reader_disagrees, more_plausible


class EscalationKind(StrEnum):
    """The CLOSED vocabulary of why a unit is escalation-pending (a CANDIDATE, not a ✔).

    Closed + totality-testable (mirrors the fold / recovery-kind inventory idiom): a
    meta-test asserts EVERY member has a corroborate path, so a newly-added reason can
    never silently lack a resolution edge. Extend this as the wiring reveals new
    escalation reasons — never route an escalation through a bare string.
    """

    #: A corrupt-font / broken-CMap read whose text layer rendered pervasively garbled
    #: (control codes / PUA glyphs); the deterministic candidate is the garbled read and
    #: a vision witness is REQUIRED before it may be trusted or superseded.
    GARBLE_READ = "garble_read"
    #: The PDF and XML witnesses proposed materially different body TEXT for a matched op
    #: (a payload divergence); which read is faithful is decided by an independent vision
    #: witness, not by preferring one deterministic lane.
    PAYLOAD_DISPUTE = "payload_dispute"
    #: A deterministic glyph-substitution repair produced a candidate that reads cleanly,
    #: but a repair is ALSO just a candidate — it needs an independent confirm before the
    #: repaired text is certified (post-repair still rides the corroborate edge).
    POST_REPAIR_UNCONFIRMED = "post_repair_unconfirmed"


@dataclass(frozen=True, slots=True)
class EscalationPending:
    """A typed pending: a fidelity-critical unit the free lane could not certify.

    ``unit_id`` is an opaque identifier (an HE id, a node path repr — the mechanism
    never parses it). ``reason`` is the honest descriptor (e.g. a ``garble_reason``
    line or a payload-dispute summary). ``region`` is an OPTIONAL opaque locator of the
    disputed region (a bbox repr, a text span, a page window) when known — the vision
    witness renders it; ``None`` when the region is the whole unit / unknown.
    ``candidate_text`` is the deterministic read the free lane produced (the candidate a
    vision read is confronted against), and ``candidate_op_summary`` an optional short
    summary of the candidate operation. ``kind`` is the closed :class:`EscalationKind`.
    """

    unit_id: str
    kind: EscalationKind
    reason: str
    region: Optional[str] = None
    candidate_text: Optional[str] = None
    candidate_op_summary: Optional[str] = None

    def to_json(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "kind": str(self.kind),
            "reason": self.reason,
            "region": self.region,
            "candidate_text": self.candidate_text,
            "candidate_op_summary": self.candidate_op_summary,
        }


@dataclass(frozen=True, slots=True)
class CorroborationReceipt:
    """The record of confronting a candidate with an INDEPENDENT vision witness.

    ``candidate`` is the deterministic read (from the pending); ``vision_read`` is the
    injected witness's independent read of the SAME region. ``agreed`` / ``verdict_changed``
    are computed ONLY from the canonical primitives (never hand-rolled). ``region`` is the
    pending's region locator, and ``witness_fingerprint`` a
    :func:`~lawvm.ingest.llm_backends.prompt_fingerprint.prompt_fingerprint` over the vision
    prompt + model id (a model/prompt swap re-keys the receipt, tying it to the determinism
    firewall). This is a RECORD, not a verdict — neither ``agreed`` alone graduates a unit
    nor does ``verdict_changed`` alone condemn it; the CALLER decides from the receipt.
    """

    unit_id: str
    kind: EscalationKind
    candidate: str
    vision_read: str
    agreed: bool
    verdict_changed: bool
    region: Optional[str]
    witness_fingerprint: str

    def to_json(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "kind": str(self.kind),
            "candidate": self.candidate,
            "vision_read": self.vision_read,
            "agreed": self.agreed,
            "verdict_changed": self.verdict_changed,
            "region": self.region,
            "witness_fingerprint": self.witness_fingerprint,
        }


#: A vision witness: given the escalation-pending (its region + candidate), return the
#: INDEPENDENT vision read of that region. Empty/whitespace means the witness could not
#: read the region — NOT agreement (never invent corroboration from silence). Same
#: injection shape as ``page_level._apply_rereads`` / ``make_vision_region_reader``: the
#: harness closes over the render substrate; the mechanism only calls it.
VisionReader = Callable[[EscalationPending], str]


def witness_fingerprint(
    *, witness_prompt: str = "", witness_model: str = "", witness_vocab: Iterable[str] = ()
) -> str:
    """The receipt's witness fingerprint via the canonical :func:`prompt_fingerprint`.

    Composes the vision PROMPT and the MODEL id (folded in as a second prompt, as the FI
    tagger folds its model into ``tagger_id``) plus the closed output vocabulary, so any
    edit to the prompt, the model, or the vocabulary MECHANICALLY re-keys every receipt
    computed under the old contract. Reinvents nothing.
    """
    return prompt_fingerprint(witness_prompt, witness_model, vocab=witness_vocab)


def corroborate(
    pending: EscalationPending,
    *,
    vision_reader: Optional[VisionReader],
    witness_prompt: str = "",
    witness_model: str = "",
    witness_vocab: Iterable[str] = (),
) -> Optional[CorroborationReceipt]:
    """Drive one escalation-pending through an injected vision witness → a receipt (or None).

    OFFLINE-SAFE, backend-gated:

      * ``vision_reader is None`` (the free offline sweep) → return ``None``. The unit
        stays escalation-pending — the honest un-resolved state, never a fabricated ✔.
      * the witness returns empty/whitespace (it could not read the region) → also
        ``None``: absence of a second read is NOT corroboration (never invent a verdict
        from silence). The unit stays escalation-pending.
      * otherwise → build the receipt from the two INDEPENDENT reads:
          - ``agreed = not cross_reader_disagrees(vision_read, candidate)`` — the reads
            materially agree (the candidate is corroborated).
          - ``verdict_changed = cross_reader_disagrees(vision_read, candidate) and
            more_plausible(vision_read, candidate)`` — the reads materially disagree AND
            the vision read is the strictly-less-implausible one, so the deterministic
            candidate was WRONG (a would-be false-exact CAUGHT). Both are read straight
            off the canonical primitives; the rule is nothing else.

    A receipt where ``verdict_changed`` means the candidate must NOT be certified exact
    (the caller supersedes it / types a witness disagreement). A receipt where ``agreed``
    means the candidate is corroborated. Neither is asserted here — this RECORDS.
    """
    if vision_reader is None:
        return None
    vision_read = vision_reader(pending) or ""
    if not vision_read.strip():
        return None
    candidate = pending.candidate_text or ""
    disagrees = cross_reader_disagrees(vision_read, candidate)
    agreed = not disagrees
    verdict_changed = disagrees and more_plausible(vision_read, candidate)
    return CorroborationReceipt(
        unit_id=pending.unit_id,
        kind=pending.kind,
        candidate=candidate,
        vision_read=vision_read,
        agreed=agreed,
        verdict_changed=verdict_changed,
        region=pending.region,
        witness_fingerprint=witness_fingerprint(
            witness_prompt=witness_prompt,
            witness_model=witness_model,
            witness_vocab=witness_vocab,
        ),
    )
