"""Owned manual-compilation claim recording a scoped application/modification overlay.

The M5 frontier family — the **non-textual application/modification overlay** the
classifier already names under OPC Drafting Guidance Part 6.9: an instrument
APPLIES / MODIFIES / EXCLUDES / DISAPPLIES / RESTRICTS a provision for a
scope/context/window *without editing its printed text* (a context-scoped reading,
not a textual amendment). Concrete witnesses from the candidate ledger:

- ``ukpga/2006/46`` s. 1297 ← ``uksi/2007/1093`` art. 10 ``modified``;
- ``ukpga/2006/46`` ss. 182-186 ← ``uksi/2008/432`` ``modified (temp.)``;
- ``ukpga/2006/46`` s. 232 ← ``uksi/2008/432`` ``excluded (temp.)``;
- ``ukpga/2006/46`` s. 754 ← ``uksi/2008/346`` ``restricted``.

Today LawVM has a *sensor* for the family: ``source_adjudication`` classifies a
non-textual-modification effect type (``modified`` / ``excluded`` / ``restricted``
/ ``applied`` / ``disapplied``) as ``uk_non_textual_modification_out_of_scope``
(also the ``application_by_reference_*`` / ``as_if_application_modification_*`` /
``application_modification_payload_*`` out-of-scope families), keeping it off
replay. This module is the missing *claim half*: an owned, typed,
deterministically-validated determination that records the overlay —
``(target, application-scope predicate, optional temporal window, modification /
exclusion / restriction kind, applying instrument)`` — as a **non-replayable typed
overlay finding**.

Contract (mirrors ``range_to_container_claim`` / ``deixis_application_claim`` /
``contingent_commencement_claim`` — the house style):

- A claim PROPOSES legal meaning (the scoped reading the overlay records). It does
  NOT mutate base text. The application dimension is a relation/edge, not a
  coordinate (``notes_internal/LEGAL_DIMENSION_KINDS.md``), so the safe default is
  leaving the base text INTACT and emitting a NON-replayable typed overlay finding
  (under-application, AGENTS.md §2.1) — NEVER a silent text mutation.
- A deterministic validator binds the claim to a REAL out-of-scope
  application/modification effect (reusing the existing ``source_adjudication``
  non-textual-modification recognizer), checks the bound effect id, REJECTS a
  textual-amendment effect, and verifies the scope predicate is well-formed against
  the target and the temporal window is coherent. It NEVER infers an overlay.
- With NO claim authored, replay is byte-unchanged: the effect stays on the manual
  frontier exactly as today, and no finding is emitted.

Composition with M6 (deixis): where the applying instrument is identified
deictically (the N4 ``applied by … (as inserted)`` case), M6's
``deixis_application_claim`` already OWNS resolving that "(as inserted)" reference
and emits a deixis-resolution finding. M5 CONSUMES that resolution: when an
overlay's applying provision is deictic, the claim carries the M6-resolved provision
eid in ``deictic_applying_provision`` and the validator reuses M6's recognizer
(``_looks_like_application_by_reference_deixis_source``) to confirm the source
genuinely carries the N4 deixis shape — it does NOT re-resolve the deixis.

Scope boundary: this module owns ONLY recording the scoped overlay observation — it
emits no text op and mutates no base text. Whether (and how) a future compiler ever
renders a scope-conditioned reading onto the printed text is out of scope; the
overlay finding is the typed input such work would consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_application_by_reference_deixis_source,
    _looks_like_non_textual_application_modification_source,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
APPLICATION_OVERLAY_CLAIM_KIND = "application_overlay"
_CLAIM_KINDS = frozenset({APPLICATION_OVERLAY_CLAIM_KIND})

# Manual-frontier rule id this claim family advertises. Registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` so the non-textual application/modification
# overlay shape advertises an owned claim template, a sibling of the existing
# ``uk_non_textual_modification_out_of_scope`` classification the family is parked
# under today.
APPLICATION_OVERLAY_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_non_textual_modification_overlay_candidate"
)

# Proof-semantic id for the claim's owned determination. Registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS``.
APPLICATION_OVERLAY_PROOF_SEMANTIC = "non_textual_application_modification_overlay"

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_application_overlay_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_application_overlay_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_application_overlay_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID = (
    "uk_application_overlay_claim_rejected_scope_consistency"
)

# Gate rule ids: the gate emits a NON-REPLAYABLE finding (never a text op).
APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID = (
    "uk_application_overlay_recorded_overlay_finding"
)
APPLICATION_OVERLAY_FINDING_WITHHELD_RULE_ID = (
    "uk_application_overlay_finding_withheld_unvalidated"
)

# Recognized overlay kinds. The validator only accepts these named kinds; it never
# infers the kind from the effect shape. They mirror the non-textual-modification
# effect verbs ``source_adjudication`` recognizes (OPC Guidance 6.9), plus the
# compound ``applied_with_modifications`` witnessed in the ``applied (with
# modifications)`` effect-type form.
OVERLAY_KIND_APPLIED = "applied"
OVERLAY_KIND_MODIFIED = "modified"
OVERLAY_KIND_EXCLUDED = "excluded"
OVERLAY_KIND_RESTRICTED = "restricted"
OVERLAY_KIND_APPLIED_WITH_MODIFICATIONS = "applied_with_modifications"
_RECOGNIZED_OVERLAY_KINDS = frozenset(
    {
        OVERLAY_KIND_APPLIED,
        OVERLAY_KIND_MODIFIED,
        OVERLAY_KIND_EXCLUDED,
        OVERLAY_KIND_RESTRICTED,
        OVERLAY_KIND_APPLIED_WITH_MODIFICATIONS,
    }
)

# Recognized application-scope predicate kinds. The scope predicate says WHICH
# reading the overlay records; the validator only accepts a named kind, never
# inferring it from the effect shape.
#   - ``for_purposes``: the overlay reads only "for the purposes of" a named
#     context/Part/provision.
#   - ``in_relation_to``: the overlay reads only "in relation to" a named class
#     of persons/cases/things.
#   - ``in_its_application_to``: the overlay reads onto a named applied-context
#     ("in its application to ...").
#   - ``unconditional``: the overlay reads onto the whole provision with no further
#     scope predicate (the bare ``modified`` / ``restricted`` form).
SCOPE_FOR_PURPOSES = "for_purposes"
SCOPE_IN_RELATION_TO = "in_relation_to"
SCOPE_IN_ITS_APPLICATION_TO = "in_its_application_to"
SCOPE_UNCONDITIONAL = "unconditional"
_RECOGNIZED_SCOPE_KINDS = frozenset(
    {
        SCOPE_FOR_PURPOSES,
        SCOPE_IN_RELATION_TO,
        SCOPE_IN_ITS_APPLICATION_TO,
        SCOPE_UNCONDITIONAL,
    }
)


@dataclass(frozen=True, slots=True)
class ApplicationOverlayClaim:
    """Owned determination recording a scoped application/modification overlay (M5).

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``application_overlay``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound effect (the
      out-of-scope non-textual application/modification effect, e.g. ``ukpga/2006/46``
      s. 1297 ← ``uksi/2007/1093`` art. 10 ``modified``).
    - ``affected_target``: the provision whose reading the overlay scopes (e.g.
      ``ukpga/2006/46`` s. 1297).
    - ``overlay_kind``: a recognized kind (applied / modified / excluded /
      restricted / applied_with_modifications).
    - ``scope_kind``: a recognized application-scope predicate kind (for-purposes /
      in-relation-to / in-its-application-to / unconditional).
    - ``scope_predicate``: the bounded surface of the scope predicate, e.g. ``for
      the purposes of Part 16`` / ``in relation to overseas companies``. Required
      unless ``scope_kind`` is ``unconditional``.
    - ``applying_instrument_id``: the instrument that applies/modifies the affected
      provision (e.g. ``uksi/2007/1093``).
    - ``applying_provision_ref``: the applying instrument's provision (e.g. ``art.
      10``).
    - ``temporal_window``: OPTIONAL bounded window surface for a temporary overlay
      (the ``(temp.)`` witnesses), e.g. ``until 1 October 2009`` / ``temp.``. Empty
      when the overlay is not time-limited.
    - ``deictic_applying_provision``: OPTIONAL — the M6-resolved concrete applying
      provision eid, present ONLY when the applying provision is identified
      deictically (the N4 ``applied by … (as inserted)`` case). When present the
      validator reuses M6's recognizer to confirm the source carries the N4 deixis
      shape; M5 does NOT re-resolve the deixis (that is M6's owned half).
    - ``source_snippet``: bounded quote of the application/modification effect
      surface the claim binds to (the ``modified`` / ``excluded (temp.)`` /
      ``restricted`` effect type or prose clause). The validator rejects the claim
      if this is not a real out-of-scope non-textual application/modification.
    - ``claimant`` / ``status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    affected_target: str
    overlay_kind: str
    scope_kind: str
    applying_instrument_id: str
    source_snippet: str
    scope_predicate: str = ""
    applying_provision_ref: str = ""
    temporal_window: str = ""
    deictic_applying_provision: str = ""
    claimant: str = ""
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "affected_target": self.affected_target,
            "overlay_kind": self.overlay_kind,
            "scope_kind": self.scope_kind,
            "applying_instrument_id": self.applying_instrument_id,
            "source_snippet": self.source_snippet,
            "scope_predicate": self.scope_predicate,
            "applying_provision_ref": self.applying_provision_ref,
            "temporal_window": self.temporal_window,
            "deictic_applying_provision": self.deictic_applying_provision,
            "claimant": self.claimant,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ApplicationOverlayClaimValidation:
    """Deterministic validation result for an application-overlay claim."""

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


def claim_from_dict(row: Any) -> ApplicationOverlayClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_application_overlay_claim``.
    """
    get = row.get
    return ApplicationOverlayClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        affected_target=str(get("affected_target") or ""),
        overlay_kind=str(get("overlay_kind") or ""),
        scope_kind=str(get("scope_kind") or ""),
        applying_instrument_id=str(get("applying_instrument_id") or ""),
        source_snippet=str(get("source_snippet") or ""),
        scope_predicate=str(get("scope_predicate") or ""),
        applying_provision_ref=str(get("applying_provision_ref") or ""),
        temporal_window=str(get("temporal_window") or ""),
        deictic_applying_provision=str(get("deictic_applying_provision") or ""),
        claimant=str(get("claimant") or ""),
        status=str(get("status") or "proposed"),
    )


def _effect_application_modification_source_text(effect: Any) -> str:
    """Best-effort source-text surface for an effect's application/modification binding.

    The ``modified`` / ``excluded (temp.)`` / ``restricted`` shape lives in the
    effect type/verb phrase or an attached source snippet; we concatenate the
    available surfaces so the binding check is robust to which surface carries it.
    The classifier is the arbiter of whether the shape is real.
    """
    parts: list[str] = []
    for attr in ("effect_type", "source_text", "raw_text", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _scope_consistency_error(claim: ApplicationOverlayClaim) -> str:
    """Return "" when the scope predicate and temporal window are coherent.

    The check requires:

    1. an unconditional overlay carries NO scope predicate (the bare ``modified`` /
       ``restricted`` form); every other scope kind carries a non-empty predicate;
    2. a scoped overlay's predicate surface genuinely carries the connective its
       scope kind names ("for the purposes", "in relation to", "in its
       application") — so the recorded scope kind matches the predicate text;
    3. a temporal window, when present, is a coherent bounded window surface (a
       ``temp``/``temporary`` marker or a date/period connective), not free text.

    The check never *infers* a scope; it only confirms an owned one is coherent
    against the target. It does not depend on a live tree (the application
    dimension is an overlay relation, not a coordinate), so there is no live-target
    stage — schema + source binding + this coherence floor.
    """
    scope_predicate = claim.scope_predicate.strip()
    if claim.scope_kind == SCOPE_UNCONDITIONAL:
        if scope_predicate:
            return (
                "unconditional overlay must not carry a scope_predicate; an "
                "unconditional reading scopes the whole provision"
            )
    else:
        if not scope_predicate:
            return (
                f"scope_kind {claim.scope_kind!r} requires a non-empty "
                f"scope_predicate naming the scoped context"
            )
        predicate_norm = " ".join(scope_predicate.lower().split())
        connective = {
            SCOPE_FOR_PURPOSES: "for the purpose",
            SCOPE_IN_RELATION_TO: "in relation to",
            SCOPE_IN_ITS_APPLICATION_TO: "in its application",
        }[claim.scope_kind]
        if connective not in predicate_norm:
            return (
                f"scope_predicate does not carry the {connective!r} connective the "
                f"scope_kind {claim.scope_kind!r} names; the recorded scope kind "
                f"does not match the predicate surface"
            )

    window = claim.temporal_window.strip()
    if window:
        window_norm = " ".join(window.lower().split())
        coherent = (
            "temp" in window_norm
            or "until" in window_norm
            or "from" in window_norm
            or "during" in window_norm
            or "while" in window_norm
            or "for so long" in window_norm
            or "for the period" in window_norm
            or "before the end of" in window_norm
            or "after the end of" in window_norm
        )
        if not coherent:
            return (
                "temporal_window is not a coherent bounded window surface (no "
                "temp/temporary marker or date/period connective)"
            )
    return ""


def validate_application_overlay_claim(
    claim: ApplicationOverlayClaim,
    *,
    effect: Optional[Any] = None,
) -> ApplicationOverlayClaimValidation:
    """Deterministically validate one application-overlay (M5) claim.

    Stages, in order:

    1. **Schema** — claim kind, ids, affected target, a recognized overlay kind, a
       recognized scope kind, the applying instrument, and a source snippet are
       well-formed; a scoped (non-unconditional) overlay carries a scope predicate;
       a deictic-applying overlay carries a resolved provision eid.
    2. **Source binding** — the claim's ``source_snippet`` matches a real
       out-of-scope non-textual application/modification effect (reusing the
       ``source_adjudication`` recognizer), rejecting free-form overrides and — by
       construction of the recognizer — plain TEXTUAL-amendment effects (insert /
       substitute / omit / repeal / renumber). When an ``effect`` is supplied, its
       ids must match the claim and its source surface must ALSO carry the
       application/modification shape. When the overlay is deictic, the source must
       ALSO carry the N4 ``(as inserted)`` deixis shape (reusing M6's recognizer);
       M5 reuses that resolution rather than re-resolving it.
    3. **Scope consistency** — the scope predicate is well-formed against the target
       (the recorded scope kind matches the predicate surface; an unconditional
       overlay carries none) and the temporal window, when present, is a coherent
       bounded window.

    The validator NEVER infers an overlay; it only accepts an owned one. Validation
    authorizes a NON-replayable typed overlay finding only — the base text is always
    left intact.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": APPLICATION_OVERLAY_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return ApplicationOverlayClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={
                "claim_kind": claim.claim_kind,
                "overlay_kind": claim.overlay_kind,
                "scope_kind": claim.scope_kind,
            },
            **base,
        )

    # 2. Source binding.
    if not _looks_like_non_textual_application_modification_source(claim.source_snippet):
        return ApplicationOverlayClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet is not a real out-of-scope non-textual "
                "application/modification effect; the claim may not record an "
                "overlay for a free-form or textual-amendment (insert/substitute/"
                "omit/repeal/renumber) effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return ApplicationOverlayClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real application/modification effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_application_modification_source_text(effect)
        if not _looks_like_non_textual_application_modification_source(effect_source):
            return ApplicationOverlayClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry the non-textual "
                    "application/modification shape; claim is not anchored to a real "
                    "out-of-scope application/modification effect"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 2b. Deixis composition (M6): a deictic-applying overlay's source must carry
    # the N4 "(as inserted)" deixis shape. M5 reuses M6's recognizer and the
    # M6-resolved provision; it does NOT re-resolve the deixis.
    if claim.deictic_applying_provision.strip():
        if not _looks_like_application_by_reference_deixis_source(claim.source_snippet):
            return ApplicationOverlayClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim carries a deictic_applying_provision but its source does "
                    "not carry the N4 application-by-reference-with-deixis shape; M5 "
                    "may only reuse an M6 deixis resolution for a real N4 deixis "
                    "effect"
                ),
                detail={
                    "deictic_applying_provision": claim.deictic_applying_provision,
                    "source_snippet": claim.source_snippet[:240],
                },
                **base,
            )

    # 3. Scope consistency.
    scope_error = _scope_consistency_error(claim)
    if scope_error:
        return ApplicationOverlayClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID,
            reason=scope_error,
            detail={
                "scope_kind": claim.scope_kind,
                "scope_predicate": claim.scope_predicate,
                "temporal_window": claim.temporal_window,
            },
            **base,
        )

    return ApplicationOverlayClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned application/modification overlay is well-formed, bound to a real "
            "out-of-scope non-textual application/modification effect, and "
            "scope-consistent (predicate matches the recorded scope kind; window "
            "coherent); it may emit a non-replayable overlay finding leaving the "
            "base text intact"
        ),
        detail={
            "affected_target": claim.affected_target,
            "overlay_kind": claim.overlay_kind,
            "scope_kind": claim.scope_kind,
            "applying_instrument_id": claim.applying_instrument_id,
            "temporal_window": claim.temporal_window,
            "deictic_applying_provision": claim.deictic_applying_provision,
        },
        **base,
    )


def _schema_error(claim: ApplicationOverlayClaim) -> str:
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
    if claim.overlay_kind not in _RECOGNIZED_OVERLAY_KINDS:
        return f"unsupported overlay_kind {claim.overlay_kind!r}"
    if claim.scope_kind not in _RECOGNIZED_SCOPE_KINDS:
        return f"unsupported scope_kind {claim.scope_kind!r}"
    if not claim.applying_instrument_id:
        return "missing applying_instrument_id"
    if not claim.source_snippet:
        return "missing source_snippet"
    if claim.scope_kind != SCOPE_UNCONDITIONAL and not claim.scope_predicate.strip():
        return (
            f"scope_kind {claim.scope_kind!r} requires a non-empty scope_predicate"
        )
    return ""


@dataclass(frozen=True, slots=True)
class ApplicationOverlayFinding:
    """A NON-replayable typed finding recording a scoped application/modification overlay.

    This is the M5 deliverable: a record (NOT a text op) that names the affected
    target, the overlay kind, the application-scope predicate, the optional temporal
    window, the applying instrument/provision, and — when the applying provision is
    deictic — the M6-resolved provision the overlay references. It leaves the base
    text intact (the application dimension is an overlay relation, not a coordinate;
    the safe under-application default, §2.1). Whether a future compiler ever renders
    a scope-conditioned reading is out of scope; this finding is the typed input.
    """

    claim_id: str
    effect_id: str
    statute_id: str
    affected_target: str
    overlay_kind: str
    scope_kind: str
    scope_predicate: str
    applying_instrument_id: str
    applying_provision_ref: str
    temporal_window: str
    deictic_applying_provision: str
    rule_id: str
    replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "statute_id": self.statute_id,
            "affected_target": self.affected_target,
            "overlay_kind": self.overlay_kind,
            "scope_kind": self.scope_kind,
            "scope_predicate": self.scope_predicate,
            "applying_instrument_id": self.applying_instrument_id,
            "applying_provision_ref": self.applying_provision_ref,
            "temporal_window": self.temporal_window,
            "deictic_applying_provision": self.deictic_applying_provision,
            "rule_id": self.rule_id,
            "replayable": self.replayable,
            "proof_semantic": APPLICATION_OVERLAY_PROOF_SEMANTIC,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


@dataclass(frozen=True, slots=True)
class ApplicationOverlayGateResult:
    """Whether a validated claim emits its recorded-overlay finding."""

    claim_id: str
    effect_id: str
    emitted: bool
    rule_id: str
    reason: str
    finding: Optional[ApplicationOverlayFinding] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def gate_application_overlay_claim(
    claim: ApplicationOverlayClaim,
    *,
    validated: bool = False,
) -> ApplicationOverlayGateResult:
    """Emit the recorded-overlay finding for a VALIDATED claim, else withhold.

    Precondition: ``validated`` reflects the result of
    ``validate_application_overlay_claim`` for this claim. Only a VALIDATED claim
    produces an ``ApplicationOverlayFinding`` — a NON-replayable typed record of the
    scoped application/modification overlay. An unvalidated/mismatched claim
    withholds (returns no finding), so absent or invalid claim ⇒ no finding and the
    base text is byte-unchanged. This gate NEVER emits a text op: the application
    dimension is an overlay relation, not a coordinate, and the safe default is
    leaving the base text intact (under-application).
    """
    if not validated:
        return ApplicationOverlayGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            rule_id=APPLICATION_OVERLAY_FINDING_WITHHELD_RULE_ID,
            reason=(
                "application-overlay claim is not validated; the finding is withheld "
                "and the effect stays on the manual frontier"
            ),
        )
    finding = ApplicationOverlayFinding(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        statute_id=claim.statute_id,
        affected_target=claim.affected_target,
        overlay_kind=claim.overlay_kind,
        scope_kind=claim.scope_kind,
        scope_predicate=claim.scope_predicate,
        applying_instrument_id=claim.applying_instrument_id,
        applying_provision_ref=claim.applying_provision_ref,
        temporal_window=claim.temporal_window,
        deictic_applying_provision=claim.deictic_applying_provision,
        rule_id=APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
    )
    return ApplicationOverlayGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        rule_id=APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
        reason=(
            "validated application-overlay claim records the scoped "
            "application/modification reading (target, scope, window, kind, applying "
            "instrument); emitting a non-replayable typed overlay finding (no "
            "base-text mutation — the application dimension is an overlay relation)"
        ),
        finding=finding,
    )
