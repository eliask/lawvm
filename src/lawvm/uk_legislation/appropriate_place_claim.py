"""Owned manual-compilation claim for a UK "appropriate place" insert.

A family of UK amendments insert material *at the appropriate place* with NO
named anchor, e.g.::

    "At the appropriate place insert— [entry]"

The alphabetical/ordering slot that "appropriate place" denotes is an EDITORIAL
determination the source does not specify. Replay cannot derive the insertion
position from the affected statute's source alone, so the effect is correctly
parked on the manual-compilation frontier (AGENTS.md §2.1) and never executed:
inferring a position from live text or oracle order would be a forbidden silent
target hijack (§1.1) / unowned migration (§1.6).

Witnesses (effect feed): Housing & Regeneration Act 2008 (``ukpga/2008/17``)
s.31(12) ← ``uksi/2018/1040``; s.276 ← ``ukpga/2014/14``.

Today LawVM has a *sensor* for this shape — the lowering tail rejects the insert
with ``uk_effect_appropriate_place_insert_rejected`` /
``..._definition_entry_insert_rejected`` (``effect_lowering_tail.py``), keeping
the row on the manual frontier
(``uk_manual_frontier_appropriate_place_definition_entry_candidate`` /
``..._index_entry_candidate``). This module is the missing *claim half*: an
owned, typed, deterministically-validated determination of the POSITION so that
— and only when — such a claim exists, lowering may emit the insert at the
claimed position.

Contract (mirrors ``contingent_commencement_claim.py`` / M1):

- A claim PROPOSES legal meaning (the resolved position). It does not directly
  mutate state.
- A deterministic validator binds the claim's bounded source snippet to a REAL
  appropriate-place insert with NO source-named anchor (rejecting free-form
  overrides and anchored inserts that lowering can already place), then checks
  the claimed position is admissible against the target list.
- A validated claim, and only a validated claim, gates the emission of an INSERT
  operation at the claimed position.
- With NO claim authored, lowering is byte-unchanged: the effect stays on the
  manual frontier exactly as today.

The owned position is one of two witnessed forms:

- a NAMED SIBLING the entry goes after (``preceding_sibling_eid``) or before
  (``following_sibling_eid``) — the reviewer has named the concrete neighbour the
  editorial "appropriate place" resolves to; or
- an explicit ALPHABETICAL-ORDER INDEX (``alphabetical_index``) into the target
  list — the slot the entry sorts into. The validator never infers either; it
  only accepts an owned one and checks it is real and not incompatibly occupied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.effect_lowering_tail import (
    _looks_like_appropriate_place_insert_text,
)
from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_definition_fragments import (
    _looks_like_appropriate_place_definition_entry_insert_text,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
APPROPRIATE_PLACE_INSERT_CLAIM_KIND = "appropriate_place_insert"
APPROPRIATE_PLACE_DEFINITION_ENTRY_CLAIM_KIND = "appropriate_place_definition_entry"
APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND = "appropriate_place_index_entry"
_CLAIM_KINDS = frozenset(
    {
        APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
        APPROPRIATE_PLACE_DEFINITION_ENTRY_CLAIM_KIND,
        APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND,
    }
)

# Manual-frontier rule ids this claim resolves. Both already advertise an owned
# claim template in ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS``; this claim supplies the
# missing POSITION fact for them.
APPROPRIATE_PLACE_DEFINITION_ENTRY_CANDIDATE_RULE_ID = (
    "uk_manual_frontier_appropriate_place_definition_entry_candidate"
)
APPROPRIATE_PLACE_INDEX_ENTRY_CANDIDATE_RULE_ID = (
    "uk_manual_frontier_appropriate_place_index_entry_candidate"
)
APPROPRIATE_PLACE_CANDIDATE_RULE_ID = "uk_manual_frontier_appropriate_place_candidate"

# Proof-semantic id for the claim's owned determination (already registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS``).
APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC = "appropriate_place_anchor_or_ordering_claim"

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_appropriate_place_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_appropriate_place_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_appropriate_place_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_POSITION_RULE_ID = "uk_appropriate_place_claim_rejected_position"

# Gate rule ids.
APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID = (
    "uk_appropriate_place_insert_emitted_at_claimed_position"
)
APPROPRIATE_PLACE_INSERT_WITHHELD_RULE_ID = (
    "uk_appropriate_place_insert_withheld_unvalidated"
)

# Position kinds. The claim owns exactly one.
POSITION_PRECEDING_SIBLING = "preceding_sibling"
POSITION_FOLLOWING_SIBLING = "following_sibling"
POSITION_ALPHABETICAL_INDEX = "alphabetical_index"
_POSITION_KINDS = frozenset(
    {POSITION_PRECEDING_SIBLING, POSITION_FOLLOWING_SIBLING, POSITION_ALPHABETICAL_INDEX}
)

# A source-named anchor token. Its PRESENCE in the source snippet means the
# insert is NOT actually anchor-free and lowering can already place it, so a
# claim must not override it. Used by the source-binding stage.
#
# Only the directional prepositions ``after`` / ``before`` (optionally
# ``immediately``) are treated as anchors. ``following`` / ``preceding`` are
# deliberately NOT in the alternation: in this corpus they overwhelmingly appear
# as the enumerator "insert the following entry/paragraph", which is NOT a sibling
# anchor. The anchor must also be immediately followed by a NUMBERED unit
# reference (a label token), so "after section 5"/"before paragraph 3(a)" match
# but "after consultation" / "before the Secretary of State" do not.
_SOURCE_NAMED_ANCHOR_RE = re.compile(
    r"\b(?:immediately\s+)?(?:after|before)\s+"
    r"(?:paragraph|sub-?paragraph|section|subsection|article|entry|item|head(?:ing)?|"
    r"definition|s\.|ss\.|para\.?|reg\.?|regulation)\s+"
    r"[\"“]?[0-9A-Za-z]",
    re.I,
)


def _looks_like_appropriate_place_source(text: str) -> bool:
    """True when *text* is a real appropriate-place insert in either shape.

    Binds via the existing manual-frontier classifiers — the general
    appropriate-place insert recognizer and the definition-entry variant. The
    classifiers are the arbiter of whether the shape is real; the claim may not
    invent the family.
    """
    return _looks_like_appropriate_place_insert_text(
        text
    ) or _looks_like_appropriate_place_definition_entry_insert_text(text)


def _source_names_an_anchor(text: str) -> bool:
    """True when the source already names a concrete sibling anchor.

    If the source itself names "after section 5 ..." the insert is deterministic
    and never reaches the manual frontier; a claim may not override it.
    """
    norm = " ".join((text or "").split())
    return bool(_SOURCE_NAMED_ANCHOR_RE.search(norm))


@dataclass(frozen=True, slots=True)
class AppropriatePlaceInsertClaim:
    """Owned determination resolving a UK appropriate-place insert's position.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``appropriate_place_insert`` /
      ``appropriate_place_definition_entry`` / ``appropriate_place_index_entry``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound effect.
    - ``target_list_eid``: the eid of the list/container the entry is inserted
      into (the "appropriate place" is a slot inside this list).
    - ``entry_label`` / ``entry_text``: the entry being inserted (its label, if
      any, and its text body). Carried so the gate can build the insert payload.
    - ``source_snippet``: bounded quote of the appropriate-place insert source the
      claim binds to. The validator rejects the claim if this snippet is not a
      real appropriate-place insert, or if it ALREADY names an anchor.
    - ``position_kind``: ``preceding_sibling`` / ``following_sibling`` /
      ``alphabetical_index`` — which owned position form is used.
    - ``preceding_sibling_eid`` / ``following_sibling_eid``: the named neighbour
      (one is set per the matching ``position_kind``).
    - ``alphabetical_index``: the explicit 0-based slot index into the target list
      (set when ``position_kind == alphabetical_index``).
    - ``claimant`` / ``status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    target_list_eid: str
    entry_label: str
    entry_text: str
    source_snippet: str
    position_kind: str
    preceding_sibling_eid: str = ""
    following_sibling_eid: str = ""
    alphabetical_index: int = -1
    claimant: str = ""
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "target_list_eid": self.target_list_eid,
            "entry_label": self.entry_label,
            "entry_text": self.entry_text,
            "source_snippet": self.source_snippet,
            "position_kind": self.position_kind,
            "preceding_sibling_eid": self.preceding_sibling_eid,
            "following_sibling_eid": self.following_sibling_eid,
            "alphabetical_index": self.alphabetical_index,
            "claimant": self.claimant,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class AppropriatePlaceClaimValidation:
    """Deterministic validation result for an appropriate-place claim."""

    claim_id: str
    statute_id: str
    effect_id: str
    validated: bool
    rule_id: str
    proof_semantic: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "claim_id": self.claim_id,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "validated": self.validated,
            "rule_id": self.rule_id,
            "proof_semantic": self.proof_semantic,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }
        for key in sorted(self.detail):
            row.setdefault(key, self.detail[key])
        return row


def claim_from_dict(row: Any) -> AppropriatePlaceInsertClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_appropriate_place_claim``.
    """
    get = row.get
    raw_index = get("alphabetical_index")
    try:
        alphabetical_index = int(raw_index) if raw_index is not None else -1
    except (TypeError, ValueError):
        alphabetical_index = -1
    return AppropriatePlaceInsertClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        target_list_eid=str(get("target_list_eid") or ""),
        entry_label=str(get("entry_label") or ""),
        entry_text=str(get("entry_text") or ""),
        source_snippet=str(get("source_snippet") or ""),
        position_kind=str(get("position_kind") or ""),
        preceding_sibling_eid=str(get("preceding_sibling_eid") or ""),
        following_sibling_eid=str(get("following_sibling_eid") or ""),
        alphabetical_index=alphabetical_index,
        claimant=str(get("claimant") or ""),
        status=str(get("status") or "proposed"),
    )


def _effect_appropriate_place_source_text(effect: Any) -> str:
    """Best-effort source-text surface for an effect's appropriate-place binding.

    The appropriate-place shape can live in the effect type/verb phrase or an
    attached source snippet; we concatenate available surfaces so the binding
    check is robust to which surface carries the phrasing. The classifier is the
    arbiter of whether the shape is real.
    """
    parts: list[str] = []
    for attr in ("source_text", "raw_text", "effect_type", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def validate_appropriate_place_claim(
    claim: AppropriatePlaceInsertClaim,
    *,
    effect: Optional[Any] = None,
    target_list: Optional[Sequence[str]] = None,
) -> AppropriatePlaceClaimValidation:
    """Deterministically validate one appropriate-place insert claim.

    Checks, in order:

    1. **Schema** — claim kind, position kind, ids, and the entry payload are
       well-formed; the named-sibling forms carry exactly the matching eid and the
       alphabetical form carries a non-negative index.
    2. **Source binding** — the claim's ``source_snippet`` matches a real
       appropriate-place insert (reusing the manual-frontier classifiers) AND does
       NOT already name a concrete anchor (an anchored insert is deterministic and
       never on the manual frontier — a claim may not override it). When an
       ``effect`` is supplied, its ids must match the claim and its source surface
       must ALSO carry the appropriate-place shape.
    3. **Position consistency** — when a ``target_list`` (the existing sibling eid
       sequence) is supplied: the target list is non-empty; a named sibling is a
       real member of it; an alphabetical index is within ``[0, len]`` (``len`` is
       the append slot); and the resolved slot is not already occupied by the
       entry's own label (an incompatible re-insert of an already-present entry).

    The validator NEVER infers a position; it only accepts an owned one.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return AppropriatePlaceClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={"claim_kind": claim.claim_kind, "position_kind": claim.position_kind},
            **base,
        )

    # 2. Source binding.
    if not _looks_like_appropriate_place_source(claim.source_snippet):
        return AppropriatePlaceClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet does not match an appropriate-place insert "
                "shape; the claim may not invent a placement for a free-form effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if _source_names_an_anchor(claim.source_snippet):
        return AppropriatePlaceClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet already names a concrete anchor; the insert "
                "is deterministic and a claim may not override a source-named anchor"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return AppropriatePlaceClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real appropriate-place insert effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_appropriate_place_source_text(effect)
        if not _looks_like_appropriate_place_source(effect_source):
            return AppropriatePlaceClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry an appropriate-place insert "
                    "shape; claim is not anchored to a real appropriate-place insert"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Position consistency.
    position_error = _position_error(claim, target_list)
    if position_error:
        return AppropriatePlaceClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_POSITION_RULE_ID,
            reason=position_error,
            detail={
                "position_kind": claim.position_kind,
                "preceding_sibling_eid": claim.preceding_sibling_eid,
                "following_sibling_eid": claim.following_sibling_eid,
                "alphabetical_index": claim.alphabetical_index,
                "target_list_size": (len(target_list) if target_list is not None else -1),
            },
            **base,
        )

    return AppropriatePlaceClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned appropriate-place position is well-formed, bound to a real "
            "anchor-free appropriate-place insert, and admissible in the target list"
        ),
        detail={
            "position_kind": claim.position_kind,
            "target_list_eid": claim.target_list_eid,
            "entry_label": claim.entry_label,
        },
        **base,
    )


def _schema_error(claim: AppropriatePlaceInsertClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.target_list_eid:
        return "missing target_list_eid"
    if not claim.source_snippet:
        return "missing source_snippet"
    if not (claim.entry_label or claim.entry_text):
        return "missing entry payload (entry_label/entry_text both empty)"
    if claim.position_kind == POSITION_PRECEDING_SIBLING:
        if not claim.preceding_sibling_eid:
            return "preceding_sibling position requires preceding_sibling_eid"
        if claim.following_sibling_eid or claim.alphabetical_index >= 0:
            return "preceding_sibling position must not carry other position forms"
        return ""
    if claim.position_kind == POSITION_FOLLOWING_SIBLING:
        if not claim.following_sibling_eid:
            return "following_sibling position requires following_sibling_eid"
        if claim.preceding_sibling_eid or claim.alphabetical_index >= 0:
            return "following_sibling position must not carry other position forms"
        return ""
    if claim.position_kind == POSITION_ALPHABETICAL_INDEX:
        if claim.alphabetical_index < 0:
            return "alphabetical_index position requires a non-negative alphabetical_index"
        if claim.preceding_sibling_eid or claim.following_sibling_eid:
            return "alphabetical_index position must not carry a named sibling"
        return ""
    return f"unsupported position_kind {claim.position_kind!r}"


def _position_error(
    claim: AppropriatePlaceInsertClaim,
    target_list: Optional[Sequence[str]],
) -> str:
    if target_list is None:
        # No live view supplied: schema already proved the owned position is
        # internally consistent. Consistency against the list is checked at the
        # gate when a list is available.
        return ""
    members = [str(m) for m in target_list]
    if not members:
        return "claimed target list is empty; the appropriate-place container does not exist"
    if claim.position_kind == POSITION_PRECEDING_SIBLING:
        if claim.preceding_sibling_eid not in members:
            return (
                f"claimed preceding sibling {claim.preceding_sibling_eid!r} is not a "
                f"member of the target list"
            )
        return ""
    if claim.position_kind == POSITION_FOLLOWING_SIBLING:
        if claim.following_sibling_eid not in members:
            return (
                f"claimed following sibling {claim.following_sibling_eid!r} is not a "
                f"member of the target list"
            )
        return ""
    # alphabetical_index: valid slots are 0..len (len == append at end).
    if claim.alphabetical_index > len(members):
        return (
            f"claimed alphabetical_index {claim.alphabetical_index} is past the end of "
            f"the target list (size {len(members)})"
        )
    # Reject an incompatible re-insert: the entry's own label already occupies the
    # list (inserting it again would duplicate a present entry).
    if claim.entry_label and claim.entry_label in members:
        return (
            f"entry label {claim.entry_label!r} is already present in the target list; "
            f"the claimed slot is occupied by the entry itself"
        )
    return ""


@dataclass(frozen=True, slots=True)
class AppropriatePlaceInsertGateResult:
    """Whether a validated claim emits its insert, and at which anchor."""

    claim_id: str
    effect_id: str
    emitted: bool
    rule_id: str
    reason: str
    anchor_eid: str = ""
    operation: Optional[LegalOperation] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "anchor_eid": self.anchor_eid,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def _claim_entry_payload(claim: AppropriatePlaceInsertClaim) -> IRNode:
    """Build the IR payload for the inserted entry from the owned claim text."""
    return IRNode(
        kind=IRNodeKind.ITEM,
        label=claim.entry_label or None,
        text=claim.entry_text,
    )


def _resolved_anchor_eid(
    claim: AppropriatePlaceInsertClaim,
    target_list: Optional[Sequence[str]],
) -> str:
    """Resolve the preceding-sibling eid the insert anchors after.

    - ``preceding_sibling``: the claimed preceding sibling.
    - ``following_sibling``: the member immediately before the claimed follower,
      or "" when the follower is first (insert at the head of the list).
    - ``alphabetical_index``: the member at ``index - 1``, or "" at index 0.
    """
    if claim.position_kind == POSITION_PRECEDING_SIBLING:
        return claim.preceding_sibling_eid
    members = [str(m) for m in (target_list or ())]
    if claim.position_kind == POSITION_FOLLOWING_SIBLING:
        if claim.following_sibling_eid in members:
            pos = members.index(claim.following_sibling_eid)
            return members[pos - 1] if pos > 0 else ""
        return ""
    # alphabetical_index
    if claim.alphabetical_index <= 0:
        return ""
    if members and claim.alphabetical_index - 1 < len(members):
        return members[claim.alphabetical_index - 1]
    return ""


def gate_appropriate_place_insert(
    claim: AppropriatePlaceInsertClaim,
    *,
    sequence: int,
    target_list: Optional[Sequence[str]] = None,
    validated: bool = False,
    source: Optional[OperationSource] = None,
) -> AppropriatePlaceInsertGateResult:
    """Emit the appropriate-place insert at the claimed position, iff validated.

    Precondition: ``validated`` reflects the result of
    ``validate_appropriate_place_claim`` for this claim. Only a VALIDATED claim
    produces an INSERT ``LegalOperation``; an unvalidated claim withholds (returns
    no operation), so an unvalidated/mismatched/occupied-slot claim never inserts.

    The emitted op targets a new entry under the claimed list, anchored after the
    resolved preceding sibling (empty anchor ⇒ insert at the head of the list).
    """
    if not validated:
        return AppropriatePlaceInsertGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            rule_id=APPROPRIATE_PLACE_INSERT_WITHHELD_RULE_ID,
            reason=(
                "appropriate-place claim is not validated; the insert is withheld "
                "and the effect stays on the manual frontier"
            ),
        )

    anchor_eid = _resolved_anchor_eid(claim, target_list)
    list_path = (("list", claim.target_list_eid),)
    target = LegalAddress(path=(*list_path, ("entry", claim.entry_label or "")))
    anchor = (
        LegalAddress(path=(*list_path, ("entry", anchor_eid))) if anchor_eid else None
    )
    operation = LegalOperation(
        op_id=f"{claim.effect_id}_appropriate_place_{claim.claim_id}",
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=target,
        payload=_claim_entry_payload(claim),
        anchor=anchor,
        source=source,
        provenance_tags=(
            "uk_manual_claim",
            APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC,
            f"position_kind:{claim.position_kind}",
        ),
        witness_rule_id=APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID,
    )
    return AppropriatePlaceInsertGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        rule_id=APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID,
        reason=(
            f"validated appropriate-place claim emits the insert at the claimed "
            f"{claim.position_kind} position"
        ),
        anchor_eid=anchor_eid,
        operation=operation,
    )
