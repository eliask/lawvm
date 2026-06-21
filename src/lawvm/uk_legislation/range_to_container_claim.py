"""Owned manual-compilation claim resolving a range of units to container members.

The ``range_to_container`` frontier family: an amendment whose target is a RANGE
of sibling units — ``"sections 3 to 7"``, ``"paragraphs (a) to (d)"``,
``"for sections 12 to 14 substitute …"`` — that must be resolved against a
CONTAINER whose member set/identity is itself uncertain. The range can cross a
container boundary, or its members may have been inserted/renumbered by a prior
program, so which CONCRETE units the range denotes is ambiguous from the source
surface alone. Resolving that ordered member set is an *owned* determination.

Today LawVM has a *sensor* for the family: ``source_adjudication`` classifies a
``substituted for sections X-Y`` effect over a part/chapter container as
``range_to_container_target_unsupported`` /
``range_to_container_target_absent`` (manual frontier
``uk_manual_frontier_range_to_container_candidate``), keeping it off replay. This
module is the missing *claim half* for the range-resolution part: an owned, typed,
deterministically-validated determination that names the container, the range
endpoints, and the concrete ordered list of member eids the range resolves to.

Contract (mirrors ``deixis_application_claim`` / ``contingent_commencement_claim``
— the house style):

- A claim PROPOSES legal meaning (which members the range denotes). It does NOT
  mutate base text. The safe default for the family is **under-application /
  non-replayable finding** (AGENTS.md §2.1): a validated claim emits a NON-
  replayable typed finding recording the resolved member set, never a text op and
  never an over-broad mutation across an uncertain container boundary.
- A deterministic validator binds the claim to a REAL range-to-container effect
  (reusing the existing ``source_adjudication`` range-to-container recognizer),
  checks the bound effect id, and — when a live container member list is supplied
  — verifies the resolved set is exactly the contiguous span between the endpoints
  with no gaps or strays. It NEVER infers the member set.
- With NO claim authored, replay is byte-unchanged: the effect stays on the manual
  frontier exactly as today, and no finding is emitted.

Scope boundary: this module owns ONLY the range-resolution half — which concrete
members the range denotes. The actual container-substitution overlay (compiling
the replacement payload onto the container) is the separate
``range_to_container_substitution`` machinery; this claim's typed finding is the
resolved member set that work consumes. The gate deliberately emits no overlay and
mutates no base text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_range_to_container_source,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
RANGE_TO_CONTAINER_CLAIM_KIND = "range_to_container_resolution"
_CLAIM_KINDS = frozenset({RANGE_TO_CONTAINER_CLAIM_KIND})

# Manual-frontier rule id this claim family advertises. Registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` so the range-to-container range-resolution
# shape advertises an owned claim template, a sibling of the existing
# ``uk_manual_frontier_range_to_container_candidate`` substitution classification
# the family is parked under today.
RANGE_TO_CONTAINER_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_range_to_container_resolution_candidate"
)

# Proof-semantic id for the claim's owned determination. Registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS``.
RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC = "range_to_container_member_resolution"

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_range_to_container_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_range_to_container_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_range_to_container_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID = (
    "uk_range_to_container_claim_rejected_member_consistency"
)

# Gate rule ids: the gate emits a NON-REPLAYABLE finding (never a text op).
RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID = (
    "uk_range_to_container_resolved_members_finding"
)
RANGE_TO_CONTAINER_FINDING_WITHHELD_RULE_ID = (
    "uk_range_to_container_finding_withheld_unvalidated"
)

# Recognized resolution bases. The validator only accepts these named kinds; it
# never infers the basis from the effect shape.
#   - ``contiguous_container_span``: the range denotes the contiguous span of
#     sibling members between its endpoints within a single container whose member
#     order is known (the canonical case).
#   - ``post_program_renumbered_span``: the container's members were renumbered or
#     inserted by a prior program, so the resolved span is taken over the live
#     (post-program) member labels rather than the source's nominal labels.
BASIS_CONTIGUOUS_CONTAINER_SPAN = "contiguous_container_span"
BASIS_POST_PROGRAM_RENUMBERED_SPAN = "post_program_renumbered_span"
_RECOGNIZED_BASES = frozenset(
    {BASIS_CONTIGUOUS_CONTAINER_SPAN, BASIS_POST_PROGRAM_RENUMBERED_SPAN}
)

# An eid path component carrying a label, e.g. the "section:7" in an eid path, or
# a bare label token. The endpoints name labels; the resolved member eids must
# carry those labels at their leaf.
_EID_LEAF_LABEL_RE = re.compile(r"([0-9A-Za-z]+)\s*$")


@dataclass(frozen=True, slots=True)
class RangeToContainerClaim:
    """Owned determination resolving a range of units to concrete container members.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``range_to_container_resolution``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound effect
      (the range-to-container substitution effect, e.g. ``substituted for
      sections 12-14``).
    - ``container_eid``: the container the range is resolved against (e.g. the
      part/chapter eid whose member set the range spans).
    - ``range_start_label`` / ``range_end_label``: the range endpoints as named in
      the source (e.g. ``"12"`` … ``"14"``, or ``"a"`` … ``"d"``).
    - ``source_snippet``: bounded quote of the range-to-container effect surface
      the claim binds to (the ``for sections X to Y substitute`` source text). The
      validator rejects the claim if this is not a real range-to-container effect.
    - ``resolved_member_eids``: the CONCRETE ordered list of member eids the range
      denotes once resolved against the container. Ordered start→end, non-empty.
    - ``resolution_basis``: a recognized basis (contiguous-container-span /
      post-program-renumbered-span).
    - ``renumbering_program_id``: the program whose renumber/insertion the
      post-program span resolves against (the "uncertain member set" source).
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    container_eid: str
    range_start_label: str
    range_end_label: str
    source_snippet: str
    resolved_member_eids: tuple[str, ...]
    resolution_basis: str
    renumbering_program_id: str = ""
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "container_eid": self.container_eid,
            "range_start_label": self.range_start_label,
            "range_end_label": self.range_end_label,
            "source_snippet": self.source_snippet,
            "resolved_member_eids": list(self.resolved_member_eids),
            "resolution_basis": self.resolution_basis,
            "renumbering_program_id": self.renumbering_program_id,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class RangeToContainerClaimValidation:
    """Deterministic validation result for a range-to-container claim."""

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


def claim_from_dict(row: Any) -> RangeToContainerClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_range_to_container_claim``.
    """
    get = row.get
    members = get("resolved_member_eids") or ()
    return RangeToContainerClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        container_eid=str(get("container_eid") or ""),
        range_start_label=str(get("range_start_label") or ""),
        range_end_label=str(get("range_end_label") or ""),
        source_snippet=str(get("source_snippet") or ""),
        resolved_member_eids=tuple(str(eid) for eid in members),
        resolution_basis=str(get("resolution_basis") or ""),
        renumbering_program_id=str(get("renumbering_program_id") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
    )


def _effect_range_to_container_source_text(
    effect: Any, extracted_source_text: Optional[str] = None
) -> str:
    """Best-effort source-text surface for an effect's range-to-container binding.

    The ``substituted for sections X-Y`` shape lives in the effect type/verb phrase
    or an attached source snippet; we concatenate the available surfaces so the
    binding check is robust to which surface carries it. The classifier is the
    arbiter of whether the shape is real.

    On REAL feed effects the effect attributes are empty — the range prose lives in
    the extracted affecting XML, which the replay pipeline passes in as
    ``extracted_source_text`` (the same surface the manual-frontier classifier
    binds). When supplied it is concatenated first; the effect attributes remain as
    a fallback so synthetic unit-fixture effects keep binding.
    """
    parts: list[str] = []
    if extracted_source_text:
        parts.append(str(extracted_source_text))
    for attr in ("effect_type", "source_text", "raw_text", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _eid_leaf_label(eid: str) -> str:
    """The leaf label token of a member eid, lowercased.

    e.g. ``"part:2/chapter:1/section:7"`` -> ``"7"``; ``"...paragraph:d"`` ->
    ``"d"``. Used to confirm an endpoint corresponds to a real member eid. Returns
    "" when no trailing label token is present.
    """
    tail = (eid or "").rsplit("/", 1)[-1]
    tail = tail.rsplit(":", 1)[-1]
    match = _EID_LEAF_LABEL_RE.search(tail)
    return match.group(1).lower() if match else ""


def _member_consistency_error(
    claim: RangeToContainerClaim,
    *,
    container_member_eids: Optional[Sequence[str]],
) -> str:
    """Return "" when the resolved set is the contiguous span between the endpoints.

    Only runs when a live ``container_member_eids`` ordered list is supplied (the
    member-consistency stage is skipped when the live container is unknown — the
    safe default leaves the schema+source binding as the floor). The check
    requires:

    1. both endpoints correspond to real container members (an eid whose leaf
       label matches ``range_start_label`` / ``range_end_label``), and
    2. the resolved member eids are exactly the contiguous span of the container's
       member order from the start endpoint through the end endpoint inclusive —
       same order, no gaps, no strays.

    The check never *infers* a member set; it only confirms an owned one is the
    contiguous span the range denotes.
    """
    members = [str(eid) for eid in (container_member_eids or ()) if str(eid)]
    if not members:
        return ""
    start_label = claim.range_start_label.strip().lower()
    end_label = claim.range_end_label.strip().lower()
    label_of = {_eid_leaf_label(eid): eid for eid in members}
    if start_label not in label_of:
        return (
            f"range_start_label {claim.range_start_label!r} is not a member of the "
            f"container; the endpoint does not name a real member"
        )
    if end_label not in label_of:
        return (
            f"range_end_label {claim.range_end_label!r} is not a member of the "
            f"container; the endpoint does not name a real member"
        )
    start_eid = label_of[start_label]
    end_eid = label_of[end_label]
    start_idx = members.index(start_eid)
    end_idx = members.index(end_eid)
    if start_idx > end_idx:
        return (
            "range_start_label follows range_end_label in the container member "
            "order; the range endpoints are inverted"
        )
    expected_span = tuple(members[start_idx : end_idx + 1])
    if tuple(claim.resolved_member_eids) != expected_span:
        return (
            "resolved_member_eids are not the contiguous container span between the "
            "endpoints; the resolution has a gap, a stray member, or is misordered"
        )
    return ""


def validate_range_to_container_claim(
    claim: RangeToContainerClaim,
    *,
    effect: Optional[Any] = None,
    container_member_eids: Optional[Sequence[str]] = None,
    extracted_source_text: Optional[str] = None,
) -> RangeToContainerClaimValidation:
    """Deterministically validate one range-to-container resolution claim.

    Stages, in order:

    1. **Schema** — claim kind, ids, container eid, both range endpoints, a non-
       empty ordered resolved member list, and a recognized resolution basis are
       well-formed; the post-program basis carries a renumbering-program id.
    2. **Source binding** — the claim's ``source_snippet`` matches a real range-to-
       container effect (reusing the ``source_adjudication`` range recognizer),
       rejecting free-form overrides and single-unit (non-range) targets. When an
       ``effect`` is supplied, its ids must match the claim and its source surface
       must ALSO carry the range-to-container shape.
    3. **Member consistency** — when a live ``container_member_eids`` ordered list
       is supplied: both endpoints are real members and the resolved set is exactly
       the contiguous span between them, with no gaps or strays.

    The validator NEVER infers a member set; it only accepts an owned one.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return RangeToContainerClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={
                "claim_kind": claim.claim_kind,
                "resolution_basis": claim.resolution_basis,
            },
            **base,
        )

    # 2. Source binding.
    if not _looks_like_range_to_container_source(claim.source_snippet):
        return RangeToContainerClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet is not a real range-to-container effect; the "
                "claim may not resolve a range for a free-form or single-unit "
                "(non-range) effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return RangeToContainerClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real range-to-container effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_range_to_container_source_text(
            effect, extracted_source_text
        )
        if not _looks_like_range_to_container_source(effect_source):
            return RangeToContainerClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry the range-to-container "
                    "shape; claim is not anchored to a real range-to-container "
                    "effect"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Member consistency (only when a live container member list is supplied).
    member_error = _member_consistency_error(
        claim, container_member_eids=container_member_eids
    )
    if member_error:
        return RangeToContainerClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID,
            reason=member_error,
            detail={
                "container_eid": claim.container_eid,
                "range_start_label": claim.range_start_label,
                "range_end_label": claim.range_end_label,
                "resolved_member_eids": list(claim.resolved_member_eids),
            },
            **base,
        )

    return RangeToContainerClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned range-to-container resolution is well-formed, bound to a real "
            "range-to-container effect, and (when checked) the resolved set is the "
            "contiguous container span between the range endpoints"
        ),
        detail={
            "container_eid": claim.container_eid,
            "range_start_label": claim.range_start_label,
            "range_end_label": claim.range_end_label,
            "resolved_member_eids": list(claim.resolved_member_eids),
            "resolution_basis": claim.resolution_basis,
        },
        **base,
    )


def _schema_error(claim: RangeToContainerClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.container_eid.strip():
        return "missing container_eid"
    if not claim.range_start_label.strip():
        return "missing range_start_label"
    if not claim.range_end_label.strip():
        return "missing range_end_label"
    if not claim.source_snippet:
        return "missing source_snippet"
    if not claim.resolved_member_eids:
        return "resolved_member_eids must be a non-empty ordered list"
    if any(not str(eid).strip() for eid in claim.resolved_member_eids):
        return "resolved_member_eids must not contain empty member eids"
    if claim.resolution_basis not in _RECOGNIZED_BASES:
        return f"unsupported resolution_basis {claim.resolution_basis!r}"
    if claim.resolution_basis == BASIS_POST_PROGRAM_RENUMBERED_SPAN:
        if not claim.renumbering_program_id:
            return (
                "post_program_renumbered_span basis requires a "
                "renumbering_program_id (the program whose renumber/insertion the "
                "live member set reflects)"
            )
    return ""


@dataclass(frozen=True, slots=True)
class RangeToContainerFinding:
    """A NON-replayable typed finding recording the resolved range member set.

    This is the deliverable: a record (NOT a text op) that names the container, the
    range endpoints, and the concrete ordered member eids the range resolves to. It
    leaves the base text intact (the safe under-application default, §2.1) and is
    the input the separate ``range_to_container_substitution`` overlay would
    consume.
    """

    claim_id: str
    effect_id: str
    statute_id: str
    container_eid: str
    range_start_label: str
    range_end_label: str
    resolved_member_eids: tuple[str, ...]
    resolution_basis: str
    renumbering_program_id: str
    rule_id: str
    replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "statute_id": self.statute_id,
            "container_eid": self.container_eid,
            "range_start_label": self.range_start_label,
            "range_end_label": self.range_end_label,
            "resolved_member_eids": list(self.resolved_member_eids),
            "resolution_basis": self.resolution_basis,
            "renumbering_program_id": self.renumbering_program_id,
            "rule_id": self.rule_id,
            "replayable": self.replayable,
            "proof_semantic": RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


@dataclass(frozen=True, slots=True)
class RangeToContainerGateResult:
    """Whether a validated claim emits its resolved-members finding."""

    claim_id: str
    effect_id: str
    emitted: bool
    rule_id: str
    reason: str
    finding: Optional[RangeToContainerFinding] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def gate_range_to_container_claim(
    claim: RangeToContainerClaim,
    *,
    validated: bool = False,
) -> RangeToContainerGateResult:
    """Emit the resolved-members finding for a VALIDATED claim, else withhold.

    Precondition: ``validated`` reflects the result of
    ``validate_range_to_container_claim`` for this claim. Only a VALIDATED claim
    produces a ``RangeToContainerFinding`` — a NON-replayable typed record of the
    ordered member eids the range denotes. An unvalidated/mismatched claim
    withholds (returns no finding), so absent or invalid claim ⇒ no finding and the
    base text is byte-unchanged. This gate NEVER emits a text op: resolving the
    range is the owned half; the container-substitution overlay is separate, and
    the safe default for an uncertain container boundary is under-application.
    """
    if not validated:
        return RangeToContainerGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            rule_id=RANGE_TO_CONTAINER_FINDING_WITHHELD_RULE_ID,
            reason=(
                "range-to-container claim is not validated; the finding is withheld "
                "and the effect stays on the manual frontier"
            ),
        )
    finding = RangeToContainerFinding(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        statute_id=claim.statute_id,
        container_eid=claim.container_eid,
        range_start_label=claim.range_start_label,
        range_end_label=claim.range_end_label,
        resolved_member_eids=claim.resolved_member_eids,
        resolution_basis=claim.resolution_basis,
        renumbering_program_id=claim.renumbering_program_id,
        rule_id=RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID,
    )
    return RangeToContainerGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        rule_id=RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID,
        reason=(
            "validated range-to-container claim resolves the range to the concrete "
            "ordered container members; emitting a non-replayable typed finding (no "
            "base-text mutation across the uncertain container boundary)"
        ),
        finding=finding,
    )
