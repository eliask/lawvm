"""``lawvm.assumption_register.v0`` — declared NON-guarantees as root-committed objects.

WHAT THIS ENABLES. A LawVM build makes many positive claims (this statute
materializes, these refs resolve, this transition is certified). It also has
**non-guarantees** — things it did NOT check, CANNOT check, checked-and-failed,
or that fall OUTSIDE the stated claim scope. Historically those non-guarantees
live as prose (``xfail(reason=...)``, ``# TODO``, a paragraph in a STATUS doc).
Prose is not checkable: nothing distinguishes "we forgot" from "we deliberately
scoped this out", and nothing forces the boundary to be revisited when the world
changes. This module makes each declared non-guarantee a **frozen, typed,
content-addressed object** with a stable id, and a ``SetRoot`` over the declared
set so a checker can detect a missing or surplus assumption — the assumption set
itself becomes an auditable artifact, not folklore.

The four positions a register entry distinguishes — the whole point of the type:

* **"we did not check"**  → ``kind="parser_incomplete"`` / ``source_unavailable``
  (a capability gap; the check is in scope but not yet run / not yet possible).
* **"we cannot check"**   → ``kind="doctrine_unresolved"`` (no compile-time
  discriminator / no oracle exists to decide it — distinct from merely unrun).
* **"checked and failed"** → NOT an assumption; that is a *finding* (see
  :mod:`lawvm.core.observation_registry`). An assumption is precisely the thing
  that is NOT a finding because it was never positively asserted.
* **"outside claim scope"** → ``effect="outside_claim"`` (we do not claim this at
  all; out of contract — neither a guarantee nor a defect).

THE LOAD-BEARING INVARIANT (mirrors signature_attestation, design §24): an
``AssumptionRegister`` lives in the **EVIDENCE / PROOF plane and MUST NOT enter
any semantic object hash**. It is computed over / about the semantic objects, but
is never a member of their identity — adding or revising a declared assumption
must NOT perturb a pack root, a statute hash, or a certificate. Its own
``assumption_id`` (a content hash) is the only hash it has; assumptions are
detached, exactly like attestations.

WHAT THIS DOES **NOT** YET DO (honesty boundary — do not overclaim):

* It does **not** auto-discover assumptions from ``xfail`` markers, ``# TODO``s,
  or prose. v0 is a **hand-curated** registry: a human encodes each declared
  non-guarantee. Nothing here scans the test suite or the source tree.
* It does **not** verify that a declared assumption is *true*, *minimal*, or
  *complete*. The register records what was DECLARED, not what is the case;
  garbage-in is possible and only ``__post_init__`` shape validation guards it.
* ``expires_when`` is a human-readable **string**, not a machine predicate — the
  register cannot itself detect that an assumption's expiry condition is met
  (e.g. "a real-corpus anchor landed"); a human or a future witness rule must.
* It is **not yet wired into the pack manifest or the compile dossier**.
  ``assumption_register_root`` exists and is deterministic, but no manifest
  member holds it and no checker consumes it yet — that integration is future
  work (the analogue of the reserved ``signatures/`` layer for attestations).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, set_root

_SCHEMA_ASSUMPTION_REGISTER = "lawvm.assumption_register.v0"
_DOMAIN_ASSUMPTION_REGISTER = "assumption_register"
_DOMAIN_ASSUMPTION_REGISTER_ROOT = "assumption_register"

# The reserved detached layer name — the analogue of ``signatures/`` (design
# §24). Reserved so a future pack can carry a declared-assumption layer whose
# omission is itself committed to (empty root). NOT wired into the manifest in
# v0 (see module docstring honesty boundary).
ASSUMPTIONS_LAYER = "assumptions"


class AssumptionRegisterError(ValueError):
    """An assumption-register object violates a v0 schema invariant."""


# --------------------------------------------------------------------------- #
# Closed vocabularies.                                                        #
# --------------------------------------------------------------------------- #

# WHY the thing is not guaranteed — the mechanism of the gap. Closed set so a
# reader can triage "did not check" (capability gap) vs "cannot check" (no
# discriminator/oracle).
AssumptionKind = Literal[
    "source_unavailable",  # the source needed to check does not exist / is not held
    "parser_incomplete",  # the check is in scope but the parser/path is not yet built
    "doctrine_unresolved",  # no compile-time discriminator / oracle decides it (cannot check)
    "projection_unverified",  # a projection/derivation is asserted but not independently verified
]
ASSUMPTION_KINDS: frozenset[str] = frozenset(
    {
        "source_unavailable",
        "parser_incomplete",
        "doctrine_unresolved",
        "projection_unverified",
    }
)

# WHAT the assumption does to the claim it touches — the contract effect.
AssumptionEffect = Literal[
    "blocks_clean",  # while live, the system cannot make a clean claim over `scope`
    "qualifies",  # the claim stands but is QUALIFIED by this declared boundary
    "outside_claim",  # `scope` is explicitly out of contract — not claimed at all
]
ASSUMPTION_EFFECTS: frozenset[str] = frozenset(
    {"blocks_clean", "qualifies", "outside_claim"}
)


# --------------------------------------------------------------------------- #
# The register entry.                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AssumptionRegister:
    """``lawvm.assumption_register.v0`` — one declared non-guarantee, detached.

    A frozen, content-addressed record that a specific non-guarantee was
    DECLARED. It lives in the evidence plane: its ``assumption_id`` is computed
    over its own body and is the ONLY hash it carries; it never enters any
    semantic object's hash (see module docstring invariant).

    Fields:

    * ``kind`` — the mechanism of the gap (``ASSUMPTION_KINDS``).
    * ``scope`` — a human-readable locator of WHAT is not guaranteed (e.g. a
      doctrine name, an address class, a test id). Free text, NFC-normalised.
    * ``effect`` — the contract effect on the touched claim (``ASSUMPTION_EFFECTS``).
    * ``expires_when`` — a human-readable condition under which this assumption
      should be revisited / removed. A STRING, not a machine predicate (boundary).
    * ``public_message`` — the honest, public-facing statement of the boundary.
    * ``witness_rule_id`` — the witness/rule this assumption is attached to, if
      any (``None`` when the assumption is not tied to a single rule).
    * ``finding_refs`` — ids of related findings/tests (e.g. the ``xfail``'d
      test), so the register cross-links to the prose it supersedes.
    """

    kind: AssumptionKind
    scope: str
    effect: AssumptionEffect
    expires_when: str
    public_message: str
    witness_rule_id: str | None = None
    finding_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in ASSUMPTION_KINDS:
            raise AssumptionRegisterError(
                f"kind must be one of {sorted(ASSUMPTION_KINDS)!r}, got {self.kind!r}"
            )
        if self.effect not in ASSUMPTION_EFFECTS:
            raise AssumptionRegisterError(
                f"effect must be one of {sorted(ASSUMPTION_EFFECTS)!r}, got {self.effect!r}"
            )
        if not self.scope or not self.scope.strip():
            raise AssumptionRegisterError("scope must be a non-empty locator string")
        if not self.expires_when or not self.expires_when.strip():
            raise AssumptionRegisterError(
                "expires_when must be a non-empty condition string — a non-guarantee "
                "with no revisit condition is folklore, not a declared assumption"
            )
        if not self.public_message or not self.public_message.strip():
            raise AssumptionRegisterError(
                "public_message must be a non-empty honest statement of the boundary"
            )
        if self.witness_rule_id is not None and not self.witness_rule_id.strip():
            raise AssumptionRegisterError(
                "witness_rule_id must be None or a non-empty rule id, not blank"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_ASSUMPTION_REGISTER,
            "kind": self.kind,
            "scope": nfc(self.scope),
            "effect": self.effect,
            "expires_when": nfc(self.expires_when),
            "public_message": nfc(self.public_message),
            "witness_rule_id": self.witness_rule_id,
            "finding_refs": list(self.finding_refs),
        }

    @property
    def assumption_id(self) -> str:
        """Content id of the assumption — its ONLY hash (never enters a semantic hash)."""
        return leaf_hash(_DOMAIN_ASSUMPTION_REGISTER, self.to_canonical_dict())


def assumption_register_root(assumptions: Sequence[AssumptionRegister]) -> str:
    """``SetRoot`` over assumption ids — the reserved ``assumption_register_root``.

    The declared-assumption SET as a single checkable root: adding, dropping, or
    editing any assumption changes the root, so a checker can detect a missing or
    surplus declared non-guarantee. Empty is a valid empty ``SetRoot`` (the v0
    case for a pack that declares no assumptions — the omission is committed to).
    Duplicate identical assumptions are rejected by ``set_root`` (an assumption
    is declared once).
    """
    return set_root(
        _DOMAIN_ASSUMPTION_REGISTER_ROOT, [a.assumption_id for a in assumptions]
    )
