"""Owned manual-compilation claim resolving the deixis in an application effect.

The N4 frontier family: **application-by-reference effects with an embedded
deixis**, e.g. the effect type::

    applied by SSI 2005/467 reg. 33(2) (as inserted)

on ``asp/2003/13`` s. 100 ← ``ssi/2017/229`` reg. 24(3) (72 such rows in the
sweep, all devolved; also ``asp/2003/13`` s. 250(7) ← ``ssi/2017/232`` reg. 8,
``applied by SSI 2008/356 reg. 8A(4) (as inserted)``). The effect *applies* the
affected provision by reference to a target provision that is itself identified
**deictically** ("(as inserted)") — so even locating the operative *applying*
rule requires resolving an amendment program in a *third* instrument.

Today LawVM has a *sensor* for the family: ``source_adjudication`` classifies an
``applied by …`` effect type as ``application_by_reference_effect_out_of_scope``
(manual frontier ``uk_manual_frontier_application_by_reference_out_of_scope``),
keeping it off replay. This module is the missing *claim half* for the
deixis-resolution part of N4: an owned, typed, deterministically-validated
determination that **resolves the "(as inserted)" reference** — naming the
applying instrument, the deictically-located applying provision, and the concrete
provision it resolves to (via which inserting amendment).

Contract (mirrors ``contingent_commencement_claim`` / M1, the house style):

- A claim PROPOSES legal meaning (the deixis resolution). It does NOT mutate base
  text. The safe default for N4 is **under-application** (AGENTS.md §2.1): a
  validated claim emits a NON-REPLAYABLE typed finding recording the resolved
  reference, never a text op.
- A deterministic validator binds the claim to a REAL N4
  application-by-reference-with-deixis effect (reusing the existing
  ``source_adjudication`` classifier), checks the bound effect id, and verifies
  the claimed resolution is reachable via the cited inserting program — reusing
  the cat-4 inserted-anchor recognizer applied to the *applying* instrument
  rather than the affected text. It NEVER infers a resolution.
- With NO claim authored, replay is byte-unchanged: the effect stays on the
  manual frontier exactly as today, and no finding is emitted.

Scope boundary (M6 vs the deferred M5): M6 owns ONLY the deixis-resolution half —
resolving which concrete applying provision "(as inserted)" denotes. The full
application-overlay (compiling the scoped reading of the affected provision onto
its text) is the deferred M5 work; M6's typed finding is the input a future M5
would consume. M6 deliberately emits no overlay and mutates no base text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_amendment_program_inserted_anchor_instruction,
    _looks_like_application_by_reference_deixis_source,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
DEIXIS_IN_APPLICATION_CLAIM_KIND = "deixis_in_application"
_CLAIM_KINDS = frozenset({DEIXIS_IN_APPLICATION_CLAIM_KIND})

# Manual-frontier rule id this claim family advertises. Registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` so the N4 application-by-reference-with-
# deixis shape advertises an owned claim template, consistent with the existing
# ``uk_manual_frontier_application_by_reference_out_of_scope`` classification the
# family is parked under today.
DEIXIS_IN_APPLICATION_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_application_by_reference_deixis_resolution_candidate"
)

# Proof-semantic id for the claim's owned determination. Registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS``.
DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC = (
    "application_by_reference_deixis_resolution"
)

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_deixis_in_application_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_deixis_in_application_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_deixis_in_application_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_RESOLUTION_RULE_ID = (
    "uk_deixis_in_application_claim_rejected_resolution"
)

# Gate rule ids: the gate emits a NON-REPLAYABLE finding (never a text op).
DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID = (
    "uk_deixis_in_application_resolved_reference_finding"
)
DEIXIS_IN_APPLICATION_FINDING_WITHHELD_RULE_ID = (
    "uk_deixis_in_application_finding_withheld_unvalidated"
)

# Recognized resolution bases. The validator only accepts these named kinds; it
# never infers the basis from the effect shape.
#   - ``inserting_amendment_program``: the deictic anchor is resolved to the
#     concrete provision inserted by a cited inserting amendment instruction in
#     the applying instrument (the canonical N4 case).
#   - ``commencement_inserted_text``: the inserted applying provision was brought
#     into force by a cited commencement instrument (a witnessed variant where the
#     insertion is commencement-gated rather than free-standing).
BASIS_INSERTING_AMENDMENT_PROGRAM = "inserting_amendment_program"
BASIS_COMMENCEMENT_INSERTED_TEXT = "commencement_inserted_text"
_RECOGNIZED_BASES = frozenset(
    {BASIS_INSERTING_AMENDMENT_PROGRAM, BASIS_COMMENCEMENT_INSERTED_TEXT}
)

# A bracketed leaf-label component, e.g. the "(2)" in "reg. 33(2)" / "8A(4)" /
# "paragraph (2)". The deixis "(as inserted)" denotes a specific inserted leaf;
# its leaf label must match what the cited inserting program inserts.
_BRACKETED_LABEL_RE = re.compile(r"\(([0-9A-Za-z]+)\)")
# An inserting instruction's inserted leaf, e.g. "insert— (2)" / "insert (2)".
_INSERTED_LEAF_RE = re.compile(r"insert(?:s|ed|ing)?\b[\s—–-]*\(([0-9A-Za-z]+)\)", re.I)


@dataclass(frozen=True, slots=True)
class DeixisInApplicationClaim:
    """Owned determination resolving the "(as inserted)" deixis of an N4 effect.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``deixis_in_application``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound N4 effect
      (the application-by-reference-with-deixis effect, e.g. ``asp/2003/13`` s.100
      ← ``ssi/2017/229`` reg. 24(3)).
    - ``affected_target``: the affected provision the effect applies by reference
      (e.g. ``asp/2003/13`` s. 100).
    - ``applying_instrument_id``: the instrument that *applies* the affected
      provision by reference (e.g. ``ssi/2005/467``).
    - ``deictic_provision_ref``: the deictically-located applying provision as
      named in the effect (e.g. ``reg. 33(2)``).
    - ``deictic_surface``: the bounded deixis snippet, e.g. ``(as inserted)``.
    - ``source_snippet``: bounded quote of the N4 effect surface the claim binds
      to (the ``applied by … (as inserted)`` effect type / source text). The
      validator rejects the claim if this is not a real N4 deixis effect.
    - ``resolved_provision_eid``: the CONCRETE provision the deixis resolves to —
      the operative applying provision once "(as inserted)" is resolved.
    - ``resolution_basis``: a recognized basis (inserting-amendment-program /
      commencement-inserted-text).
    - ``inserting_instrument_id``: the instrument whose amendment inserted the
      applying provision (the "third instrument").
    - ``inserting_amendment_ref``: the inserting amendment provision/instruction
      reference (e.g. ``reg. 5(3)`` of the inserting instrument).
    - ``inserting_program_snippet``: bounded quote of the inserting instruction
      surface, used to prove the resolution is reachable (reuses the cat-4
      inserted-anchor recognizer applied to the APPLYING instrument).
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    affected_target: str
    applying_instrument_id: str
    deictic_provision_ref: str
    deictic_surface: str
    source_snippet: str
    resolved_provision_eid: str
    resolution_basis: str
    inserting_instrument_id: str = ""
    inserting_amendment_ref: str = ""
    inserting_program_snippet: str = ""
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "affected_target": self.affected_target,
            "applying_instrument_id": self.applying_instrument_id,
            "deictic_provision_ref": self.deictic_provision_ref,
            "deictic_surface": self.deictic_surface,
            "source_snippet": self.source_snippet,
            "resolved_provision_eid": self.resolved_provision_eid,
            "resolution_basis": self.resolution_basis,
            "inserting_instrument_id": self.inserting_instrument_id,
            "inserting_amendment_ref": self.inserting_amendment_ref,
            "inserting_program_snippet": self.inserting_program_snippet,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class DeixisInApplicationClaimValidation:
    """Deterministic validation result for a deixis-in-application claim."""

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


def claim_from_dict(row: Any) -> DeixisInApplicationClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_deixis_in_application_claim``.
    """
    get = row.get
    return DeixisInApplicationClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        affected_target=str(get("affected_target") or ""),
        applying_instrument_id=str(get("applying_instrument_id") or ""),
        deictic_provision_ref=str(get("deictic_provision_ref") or ""),
        deictic_surface=str(get("deictic_surface") or ""),
        source_snippet=str(get("source_snippet") or ""),
        resolved_provision_eid=str(get("resolved_provision_eid") or ""),
        resolution_basis=str(get("resolution_basis") or ""),
        inserting_instrument_id=str(get("inserting_instrument_id") or ""),
        inserting_amendment_ref=str(get("inserting_amendment_ref") or ""),
        inserting_program_snippet=str(get("inserting_program_snippet") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
    )


def _effect_deixis_application_source_text(
    effect: Any, extracted_source_text: Optional[str] = None
) -> str:
    """Best-effort source-text surface for an effect's N4 deixis binding.

    The ``applied by … (as inserted)`` shape lives in the effect type/verb phrase
    or an attached source snippet; we concatenate the available surfaces so the
    binding check is robust to which surface carries it. The classifier is the
    arbiter of whether the shape is real.

    On REAL feed effects the effect attributes are empty — the N4 deixis prose
    lives in the extracted affecting XML, which the replay pipeline passes in as
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


def _leaf_label(ref: str) -> str:
    """The innermost bracketed leaf label of a provision reference.

    e.g. ``reg. 33(2)`` -> ``"2"``; ``reg. 8A(4)`` -> ``"4"``; ``paragraph (2)``
    -> ``"2"``. The deixis "(as inserted)" denotes a specific inserted leaf; this
    is the label whose insertion the cited program must witness. Returns "" when
    no bracketed label is present.
    """
    matches = _BRACKETED_LABEL_RE.findall(ref or "")
    return matches[-1].lower() if matches else ""


def _inserted_leaf_labels(snippet: str) -> set[str]:
    """The leaf labels a recognized inserting instruction inserts.

    e.g. ``... insert— (2) the body`` -> ``{"2"}``. Used to confirm the cited
    inserting program inserts the leaf the deixis denotes.
    """
    return {m.lower() for m in _INSERTED_LEAF_RE.findall(snippet or "")}


def _resolution_reachable_via_inserting_program(
    claim: DeixisInApplicationClaim,
) -> str:
    """Return "" when the claimed resolution is reachable via the cited program.

    Reuses the cat-4 inserted-anchor recognizer
    (``_looks_like_amendment_program_inserted_anchor_instruction``) applied to the
    APPLYING instrument's inserting instruction surface — the M6 composition the
    note describes ("the cat-4 deixis proof applied to the *applying* instrument
    rather than the affected text"). The check requires:

    1. the deictic provision and the resolved provision eid share the same leaf
       label (the resolution corresponds to the deixis), and
    2. for the inserting-amendment-program basis: the inserting snippet genuinely
       recognizes as an amendment-program inserted-anchor instruction AND inserts
       that leaf label (the program inserts what the deixis denotes).

    For the commencement-inserted-text basis, requirement (2) is relaxed: the
    insertion is witnessed by a cited commencement instrument rather than an
    inline program fragment, but the leaf-label correspondence in (1) still holds.
    The check never *infers* a resolution; it only confirms an owned one is
    reachable.
    """
    deictic_leaf = _leaf_label(claim.deictic_provision_ref)
    resolved_leaf = _leaf_label(claim.resolved_provision_eid)
    if not deictic_leaf:
        return "deictic_provision_ref carries no bracketed leaf label to resolve"
    if not resolved_leaf:
        return "resolved_provision_eid carries no bracketed leaf label"
    if deictic_leaf != resolved_leaf:
        return (
            "resolved_provision_eid leaf label does not match the deictic provision "
            "the effect names; the resolution does not correspond to the deixis"
        )

    if claim.resolution_basis == BASIS_INSERTING_AMENDMENT_PROGRAM:
        snippet = claim.inserting_program_snippet
        if not _looks_like_amendment_program_inserted_anchor_instruction(snippet):
            return (
                "inserting_program_snippet is not a recognized amendment-program "
                "inserted-anchor instruction; the deixis resolution is not reachable "
                "via the cited inserting program"
            )
        inserted = _inserted_leaf_labels(snippet)
        if inserted and deictic_leaf not in inserted:
            return (
                "the cited inserting-program surface does not insert the deictic "
                "leaf label; the program does not insert what the deixis denotes"
            )
    return ""


def validate_deixis_in_application_claim(
    claim: DeixisInApplicationClaim,
    *,
    effect: Optional[Any] = None,
    extracted_source_text: Optional[str] = None,
) -> DeixisInApplicationClaimValidation:
    """Deterministically validate one deixis-in-application claim.

    Stages, in order:

    1. **Schema** — claim kind, ids, the applying instrument / deictic provision /
       resolved provision references, the deixis surface, and a recognized
       resolution basis are well-formed; the inserting-amendment basis carries an
       inserting instrument and program snippet.
    2. **Source binding** — the claim's ``source_snippet`` matches a real N4
       application-by-reference-WITH-DEIXIS effect (reusing the existing
       ``source_adjudication`` classifier), rejecting free-form overrides and
       plain (non-deictic) applications. When an ``effect`` is supplied, its ids
       must match the claim and its source surface must ALSO carry the N4 deixis
       shape.
    3. **Resolution consistency** — the claimed resolution must be reachable via
       the cited inserting program (reusing the cat-4 inserted-anchor recognizer
       applied to the *applying* instrument), and be label-consistent with the
       deictic provision the effect names.

    The validator NEVER infers a resolution; it only accepts an owned one.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return DeixisInApplicationClaimValidation(
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
    if not _looks_like_application_by_reference_deixis_source(claim.source_snippet):
        return DeixisInApplicationClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet is not a real application-by-reference-with-"
                "deixis (N4) effect; the claim may not resolve a deixis for a "
                "free-form or non-deictic effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return DeixisInApplicationClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real N4 deixis-application effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_deixis_application_source_text(
            effect, extracted_source_text
        )
        if not _looks_like_application_by_reference_deixis_source(effect_source):
            return DeixisInApplicationClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry the N4 application-by-"
                    "reference-with-deixis shape; claim is not anchored to a real "
                    "deixis-application effect"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Resolution consistency.
    resolution_error = _resolution_reachable_via_inserting_program(claim)
    if resolution_error:
        return DeixisInApplicationClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_RESOLUTION_RULE_ID,
            reason=resolution_error,
            detail={
                "resolution_basis": claim.resolution_basis,
                "deictic_provision_ref": claim.deictic_provision_ref,
                "resolved_provision_eid": claim.resolved_provision_eid,
                "inserting_instrument_id": claim.inserting_instrument_id,
            },
            **base,
        )

    return DeixisInApplicationClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned deixis-in-application resolution is well-formed, bound to a real "
            "N4 application-by-reference-with-deixis effect, and reachable via the "
            "cited inserting program in the applying instrument"
        ),
        detail={
            "applying_instrument_id": claim.applying_instrument_id,
            "deictic_provision_ref": claim.deictic_provision_ref,
            "resolved_provision_eid": claim.resolved_provision_eid,
            "resolution_basis": claim.resolution_basis,
            "inserting_instrument_id": claim.inserting_instrument_id,
        },
        **base,
    )


def _schema_error(claim: DeixisInApplicationClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.affected_target.strip():
        return "missing affected_target"
    if not claim.applying_instrument_id:
        return "missing applying_instrument_id"
    if not claim.deictic_provision_ref.strip():
        return "missing deictic_provision_ref"
    if "inserted" not in (claim.deictic_surface or "").lower():
        return (
            f"deictic_surface {claim.deictic_surface!r} is not an '(as inserted)' "
            f"deixis"
        )
    if not claim.source_snippet:
        return "missing source_snippet"
    if not claim.resolved_provision_eid.strip():
        return "missing resolved_provision_eid"
    if claim.resolution_basis not in _RECOGNIZED_BASES:
        return f"unsupported resolution_basis {claim.resolution_basis!r}"
    if claim.resolution_basis == BASIS_INSERTING_AMENDMENT_PROGRAM:
        if not claim.inserting_instrument_id:
            return (
                "inserting_amendment_program basis requires an "
                "inserting_instrument_id"
            )
        if not claim.inserting_program_snippet.strip():
            return (
                "inserting_amendment_program basis requires an "
                "inserting_program_snippet"
            )
    if claim.resolution_basis == BASIS_COMMENCEMENT_INSERTED_TEXT:
        if not claim.inserting_instrument_id:
            return (
                "commencement_inserted_text basis requires an "
                "inserting_instrument_id (the commencement/inserting instrument)"
            )
    return ""


@dataclass(frozen=True, slots=True)
class DeixisInApplicationFinding:
    """A NON-replayable typed finding recording a resolved deixis reference.

    This is the M6 deliverable: a record (NOT a text op) that names the applying
    instrument, the deictically-located applying provision, and the concrete
    provision "(as inserted)" resolves to via the cited inserting amendment. It
    leaves the base text intact (the safe N4 default, §2.1) and is the input a
    future M5 application-overlay would consume.
    """

    claim_id: str
    effect_id: str
    statute_id: str
    affected_target: str
    applying_instrument_id: str
    deictic_provision_ref: str
    resolved_provision_eid: str
    resolution_basis: str
    inserting_instrument_id: str
    inserting_amendment_ref: str
    rule_id: str
    replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "statute_id": self.statute_id,
            "affected_target": self.affected_target,
            "applying_instrument_id": self.applying_instrument_id,
            "deictic_provision_ref": self.deictic_provision_ref,
            "resolved_provision_eid": self.resolved_provision_eid,
            "resolution_basis": self.resolution_basis,
            "inserting_instrument_id": self.inserting_instrument_id,
            "inserting_amendment_ref": self.inserting_amendment_ref,
            "rule_id": self.rule_id,
            "replayable": self.replayable,
            "proof_semantic": DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


@dataclass(frozen=True, slots=True)
class DeixisInApplicationGateResult:
    """Whether a validated claim emits its resolved-deixis finding."""

    claim_id: str
    effect_id: str
    emitted: bool
    rule_id: str
    reason: str
    finding: Optional[DeixisInApplicationFinding] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def gate_deixis_in_application_claim(
    claim: DeixisInApplicationClaim,
    *,
    validated: bool = False,
) -> DeixisInApplicationGateResult:
    """Emit the resolved-deixis finding for a VALIDATED claim, else withhold.

    Precondition: ``validated`` reflects the result of
    ``validate_deixis_in_application_claim`` for this claim. Only a VALIDATED claim
    produces a ``DeixisInApplicationFinding`` — a NON-replayable typed record of
    the resolved "(as inserted)" reference. An unvalidated/mismatched claim
    withholds (returns no finding), so absent or invalid claim ⇒ no finding and
    the base text is byte-unchanged. This gate NEVER emits a text op: M6 is the
    deixis-resolution half; the application overlay is the deferred M5.
    """
    if not validated:
        return DeixisInApplicationGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            rule_id=DEIXIS_IN_APPLICATION_FINDING_WITHHELD_RULE_ID,
            reason=(
                "deixis-in-application claim is not validated; the finding is "
                "withheld and the effect stays on the manual frontier"
            ),
        )
    finding = DeixisInApplicationFinding(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        statute_id=claim.statute_id,
        affected_target=claim.affected_target,
        applying_instrument_id=claim.applying_instrument_id,
        deictic_provision_ref=claim.deictic_provision_ref,
        resolved_provision_eid=claim.resolved_provision_eid,
        resolution_basis=claim.resolution_basis,
        inserting_instrument_id=claim.inserting_instrument_id,
        inserting_amendment_ref=claim.inserting_amendment_ref,
        rule_id=DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
    )
    return DeixisInApplicationGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        rule_id=DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
        reason=(
            "validated deixis-in-application claim resolves the '(as inserted)' "
            "reference to the concrete applying provision via the cited inserting "
            "program; emitting a non-replayable typed finding (no base-text mutation)"
        ),
        finding=finding,
    )
