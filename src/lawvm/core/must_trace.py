"""``lawvm.must_trace.v0`` — the MUST-trace ledger + drift detector (Pro §13 step 5).

WHAT THIS ENABLES. The Pro invariant-mining doc (§13 step 5) states the
discipline: *every normative ``MUST`` in the specs should map to one of —
an invariant id, a checker step, a writer refusal, or a declared
non-guarantee.* A ``MUST`` that maps to NOTHING is an unenforced normative
claim — spec poetry. This module turns that discipline into a CURATED,
VERSIONED LEDGER (one :class:`MustClause` per in-scope normative MUST, each
bound to a real target) plus a companion DRIFT DETECTOR
(:mod:`tests.test_must_trace`) that fails when a NEW unmapped MUST appears in
an in-scope spec file. It is deliberately the same shape as
:mod:`lawvm.core.claim_surface_manifest` /
:mod:`lawvm.core.assumption_register`: a hand-curated frozen set + a root +
a fail-loud gate, NOT magic NLP.

THE MAPPING KINDS (Pro §13 step 5, plus the honest fifth). Each normative MUST
lands in exactly one:

* ``invariant_id`` — maps to an :class:`~lawvm.core.invariant_spec.InvariantSpec`
  row id in :data:`~lawvm.core.invariant_spec.V0_INVARIANTS` (asserts the
  invariant EXISTS — not that it is semantically complete).
* ``checker_step`` — a dotted ``module:symbol`` (or bare module) ref to a live
  checker / gate / ratchet that verifies the MUST.
* ``writer_refusal`` — a dotted ref to a constructor / producer that RAISES on
  the bad shape (the firewall pattern).
* ``declared_non_guarantee`` — a stable handle into the AssumptionRegister set
  (here, the FI register via :func:`lawvm.finland.fi_assumptions.
  build_fi_assumption_register`, matched by ``witness_rule_id`` or a scope
  substring) — the MUST is a declared BOUNDARY, not an enforced guarantee.
* ``deferred_with_owner`` — **the honest finding bucket.** The MUST is currently
  UNENFORCED: no live invariant / checker / refusal / declared-non-guarantee
  holds it. ``target_ref`` names the owner + the reason. A ``deferred_with_owner``
  row is a TRACKED GAP, not a satisfied requirement.

HONESTY BOUNDARY (constructive-invariant pattern — read before trusting this).

* The linter ranges over a DECLARED, VERSIONED SUBSET of spec files
  (:data:`MUST_TRACE_V0_IN_SCOPE_FILES`), NOT all 66 ``notes/*.md`` and NOT
  source docstrings. Expanding the scope is a version bump, exactly like
  :data:`~lawvm.core.claim_surface_manifest.CLAIM_SURFACE_VERSION`.
* A ``deferred_with_owner`` mapping means the MUST is currently UNENFORCED — an
  honest gap with a named owner, NOT a satisfied requirement. Counting these is
  the whole point: they are findings.
* Mapping a MUST to an ``invariant_id`` asserts that invariant EXISTS in the v0
  invariant set; it does NOT assert the invariant is semantically complete or
  that the MUST is fully discharged by it.
* This module NEVER asserts "every MUST in the repo is enforced." It asserts
  only: "every normative MUST in the DECLARED in-scope file set at THIS version
  is REPRESENTED in this ledger and lands in exactly one mapping kind." That is
  the drift ratchet, not a completeness proof.

The ledger is a DECLARATION-plane object: computed ABOUT the specs, it never
enters any semantic object's hash. Its only hash is :attr:`MustTraceLedger.
ledger_root` (a ``MapRoot`` over ``{must_id: clause_body_hash}``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, map_root

_SCHEMA_MUST_CLAUSE = "lawvm.must_clause.v0"
_SCHEMA_MUST_TRACE_LEDGER = "lawvm.must_trace_ledger.v0"
_DOMAIN_MUST_CLAUSE = "must_clause"
_DOMAIN_MUST_TRACE_LEDGER_ROOT = "must_trace_ledger"

#: The MUST-trace ledger schema version (Pro §12 — versioned, claim-relative).
MUST_TRACE_VERSION = "v0"

#: The DECLARED in-scope spec-file subset for v0 (paths relative to repo root).
#: The linter ranges over exactly these files — NOT all of ``notes/*.md`` and
#: NOT source docstrings. Chosen because every normative MUST in this file maps
#: cleanly onto the existing invariant/checker/assumption backbone; the
#: certificate/seam specs (with ~70 / ~16 MUSTs) are a future version's scope.
MUST_TRACE_V0_IN_SCOPE_FILES: tuple[str, ...] = ("notes/LAWVM_PIPELINE_CONTRACT.md",)

# The closed set of mapping kinds (Pro §13 step 5 + the honest deferred bucket).
MappingKind = Literal[
    "invariant_id",
    "checker_step",
    "writer_refusal",
    "declared_non_guarantee",
    "deferred_with_owner",
]
MAPPING_KINDS: frozenset[str] = frozenset(
    {
        "invariant_id",
        "checker_step",
        "writer_refusal",
        "declared_non_guarantee",
        "deferred_with_owner",
    }
)

#: The mapping kinds that denote a LIVE accounting path (the MUST is held to
#: account by a real artifact). ``deferred_with_owner`` is deliberately EXCLUDED
#: — it is an honest UNENFORCED gap, a finding, not a live path.
ENFORCED_MAPPING_KINDS: frozenset[str] = frozenset(
    {"invariant_id", "checker_step", "writer_refusal", "declared_non_guarantee"}
)


class MustTraceError(ValueError):
    """A MUST-trace object violates a v0 schema invariant."""


@dataclass(frozen=True, slots=True)
class MustClause:
    """``lawvm.must_clause.v0`` — one normative MUST mapped to one target.

    Fields:

    * ``must_id`` — stable id for the clause (e.g. ``PIPE-MUST-01``).
    * ``spec_source`` — the in-scope spec file path the MUST appears in.
    * ``excerpt`` — the normative sentence (the actual MUST text, NFC-normalised),
      so the ledger is self-evidencing: a reader sees the requirement, not a code.
    * ``mapping_kind`` — exactly one of :data:`MAPPING_KINDS`.
    * ``target_ref`` — the bound target: an InvariantSpec id (``invariant_id``); a
      dotted ``module:symbol`` / module ref (``checker_step`` / ``writer_refusal``);
      an AssumptionRegister handle (``declared_non_guarantee``); or an
      ``owner — reason`` string (``deferred_with_owner``).
    """

    must_id: str
    spec_source: str
    excerpt: str
    mapping_kind: MappingKind
    target_ref: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("must_id", self.must_id),
            ("spec_source", self.spec_source),
            ("excerpt", self.excerpt),
            ("target_ref", self.target_ref),
        ):
            if not value or not value.strip():
                raise MustTraceError(
                    f"MustClause {self.must_id!r} {field_name} must be a non-empty string "
                    f"— a MUST that maps to nothing is the exact debt this ledger surfaces"
                )
        if self.mapping_kind not in MAPPING_KINDS:
            raise MustTraceError(
                f"MustClause {self.must_id!r} mapping_kind must be one of "
                f"{sorted(MAPPING_KINDS)!r}, got {self.mapping_kind!r}"
            )

    @property
    def is_enforced(self) -> bool:
        """True iff the clause lands in a LIVE accounting path (not deferred)."""
        return self.mapping_kind in ENFORCED_MAPPING_KINDS

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_MUST_CLAUSE,
            "must_id": nfc(self.must_id),
            "spec_source": nfc(self.spec_source),
            "excerpt": nfc(self.excerpt),
            "mapping_kind": self.mapping_kind,
            "target_ref": nfc(self.target_ref),
        }

    @property
    def clause_body_hash(self) -> str:
        return leaf_hash(_DOMAIN_MUST_CLAUSE, self.to_canonical_dict())


@dataclass(frozen=True, slots=True)
class MustTraceLedger:
    """``lawvm.must_trace_ledger.v0`` — the curated MUST-trace ledger.

    Carries the ``(must_trace_version, in_scope_files)`` pair: the ledger is
    complete RELATIVE to the declared in-scope files at this version, never
    absolute. :attr:`ledger_root` is a ``MapRoot`` over
    ``{must_id: clause_body_hash}`` so adding / dropping / editing any clause
    changes the root.
    """

    clauses: tuple[MustClause, ...]
    must_trace_version: str = MUST_TRACE_VERSION
    in_scope_files: tuple[str, ...] = MUST_TRACE_V0_IN_SCOPE_FILES

    def __post_init__(self) -> None:
        if not isinstance(self.clauses, tuple):
            raise MustTraceError("MustTraceLedger.clauses must be a tuple")
        seen: set[str] = set()
        for clause in self.clauses:
            if not isinstance(clause, MustClause):
                raise MustTraceError(
                    f"MustTraceLedger.clauses member is not a MustClause: {clause!r}"
                )
            if clause.must_id in seen:
                raise MustTraceError(
                    f"duplicate must_id {clause.must_id!r} in MustTraceLedger"
                )
            seen.add(clause.must_id)
        if not self.must_trace_version or not self.must_trace_version.strip():
            raise MustTraceError("MustTraceLedger.must_trace_version must be non-empty")
        if not isinstance(self.in_scope_files, tuple) or not self.in_scope_files:
            raise MustTraceError(
                "MustTraceLedger.in_scope_files must be a non-empty tuple "
                "— the declared scope is what makes completeness honest (Pro §12)"
            )

    def for_kind(self, mapping_kind: str) -> tuple[MustClause, ...]:
        return tuple(c for c in self.clauses if c.mapping_kind == mapping_kind)

    @property
    def deferred(self) -> tuple[MustClause, ...]:
        """The UNENFORCED MUSTs (``deferred_with_owner``) — the findings."""
        return self.for_kind("deferred_with_owner")

    @property
    def enforced(self) -> tuple[MustClause, ...]:
        """The MUSTs that land in a live accounting path."""
        return tuple(c for c in self.clauses if c.is_enforced)

    @property
    def ledger_root(self) -> str:
        return map_root(
            _DOMAIN_MUST_TRACE_LEDGER_ROOT,
            {c.must_id: c.clause_body_hash for c in self.clauses},
        )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_MUST_TRACE_LEDGER,
            "must_trace_version": self.must_trace_version,
            "in_scope_files": list(self.in_scope_files),
            "ledger_root": self.ledger_root,
            "clauses": [c.to_canonical_dict() for c in self.clauses],
        }

    def __len__(self) -> int:
        return len(self.clauses)


# --------------------------------------------------------------------------- #
# The v0 curated ledger.                                                       #
#                                                                              #
# Each row maps ONE normative MUST in notes/LAWVM_PIPELINE_CONTRACT.md to a    #
# REAL target. Where a MUST currently maps to NOTHING, it is recorded as       #
# ``deferred_with_owner`` with a precise owner + reason — NEVER given a fake    #
# mapping. The drift detector (tests/test_must_trace.py) re-scans the file and #
# asserts every normative MUST is represented here + every target resolves.    #
# --------------------------------------------------------------------------- #

_C = "notes/LAWVM_PIPELINE_CONTRACT.md"

# §2 — "Each waist MUST eventually return StageResult[T] = {...}". The spec
# itself states this is the AUDIT BACKLOG ("the gap ... IS the audit backlog";
# "TODAY most return only value"). Unenforced by construction across all waists.
_MUST_WAIST_STAGERESULT = MustClause(
    must_id="PIPE-MUST-01",
    spec_source=_C,
    excerpt=(
        "Each waist MUST eventually return `StageResult[T] = {value, evidence, "
        "residuals, findings, coverage, authority}`."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm.core.stage_result (StageResult exists); reason=the universal "
        "per-waist StageResult return is the declared audit backlog — most waists "
        "still return bare value + side-channels (spec §2: 'the gap ... IS the "
        "audit backlog'), so no live check enforces total adoption"
    ),
)

# §5 — "Every accounting object MUST classify into one of these closed sets —
# is_partition() ... is the checkable form". Live checkable form on StageResult.
_MUST_ACCOUNTING_PARTITION = MustClause(
    must_id="PIPE-MUST-02",
    spec_source=_C,
    excerpt=(
        "Every accounting object MUST classify into one of these closed sets — "
        "`is_partition()` (buckets sum to total) is the checkable form; an "
        "empty/unknown bucket forces a BLOCKED status, never silent."
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.core.stage_result:CoverageCertificate.is_partition",
)

# §5 — "unowned_violation MUST → 0; is_clean() asserts it". Live checkable form
# on the CoverageCertificate (the per-stage token/source-span accounting object).
_MUST_UNOWNED_VIOLATION_ZERO = MustClause(
    must_id="PIPE-MUST-03",
    spec_source=_C,
    excerpt="unowned_violation MUST → 0; `is_clean()` asserts it).",
    mapping_kind="checker_step",
    target_ref="lawvm.core.stage_result:CoverageCertificate.is_clean",
)

# §6 — "every code in FINDING_REGISTRY with default_enforcement in {hard_fail,
# ...blocking} MUST appear in FIRE_DRILLS or in the ... NO_FIRE_DRILL_YET
# allowlist". The live gate is the guard-liveness totality test (XP-05).
_MUST_BLOCKING_CODE_DRILLED = MustClause(
    must_id="PIPE-MUST-04",
    spec_source=_C,
    excerpt=(
        "every code in `FINDING_REGISTRY` with `default_enforcement in {hard_fail, "
        "...blocking}` MUST appear in `FIRE_DRILLS` or in the consciously-maintained "
        "`NO_FIRE_DRILL_YET` allowlist (debt, not silence)."
    ),
    mapping_kind="checker_step",
    target_ref="tests.test_guard_liveness_totality",
)

# §6 — "A fire-drill MUST drive the production guard that decides to emit".
_MUST_FIRE_DRILL_DRIVES_PRODUCTION = MustClause(
    must_id="PIPE-MUST-05",
    spec_source=_C,
    excerpt=(
        "A fire-drill MUST drive the **production guard that decides to emit**, not "
        "hand-construct the Finding (verdict-mapping-only drills are SECONDARY)."
    ),
    mapping_kind="checker_step",
    target_ref="tests.test_guard_liveness_totality",
)

# §6 — "A blocking-registered code whose only producer emits it non-blocking
# off-pipeline ... MUST be reconciled ... never left."
_MUST_REGISTRY_PRODUCER_RECONCILED = MustClause(
    must_id="PIPE-MUST-06",
    spec_source=_C,
    excerpt=(
        "A blocking-registered code whose only producer emits it non-blocking "
        "off-pipeline is a registry/producer mismatch and MUST be reconciled "
        "(downgrade the registry entry OR wire a production blocking emit), never "
        "left."
    ),
    mapping_kind="checker_step",
    target_ref="tests.test_guard_liveness_totality",
)

# §7 — "confidence MUST NOT branch replay/legal-state control flow". Live ratchet
# gate (audit registry OV-03) over src/lawvm/{core,finland}.
_MUST_CONFIDENCE_NOT_CONTROL = MustClause(
    must_id="PIPE-MUST-07",
    spec_source=_C,
    excerpt="`confidence` MUST NOT branch replay/legal-state control flow.",
    mapping_kind="checker_step",
    target_ref="tests.test_confidence_control_ratchet",
)

# §7 — "Projection rows MUST NOT carry author-set replay_authorized=True ...".
# The live firewall: the surface assembler RAISES AuthorityFirewallError on any
# node/edge with replay_authorized=True (the writer refusal that backs the rule).
_MUST_PROJECTION_NO_AUTHORITY = MustClause(
    must_id="PIPE-MUST-08",
    spec_source=_C,
    excerpt=(
        "Projection rows MUST NOT carry author-set `replay_authorized=True` / "
        "review/validator-status minted at projection time from deterministic "
        "extraction (current fi_refs violation)."
    ),
    mapping_kind="writer_refusal",
    target_ref="lawvm.core.legal_surface_assembler:AuthorityFirewallError",
)

# §8 — "Corrected source bytes MUST be re-bound to a new DigestWitness (pre/post
# pair)." No live cross-tree lint enforces the re-binding obligation.
_MUST_CORRECTED_BYTES_REBOUND = MustClause(
    must_id="PIPE-MUST-09",
    spec_source=_C,
    excerpt=(
        "Corrected source bytes MUST be re-bound to a new DigestWitness (pre/post "
        "pair)."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm.core.write_receipt / DigestWitness producers; reason=the "
        "pre/post DigestWitness re-binding on a source correction is a documented "
        "obligation with no live gate asserting that a byte correction produced a "
        "fresh witness pair — unenforced, tracked"
    ),
)

# §9 — "The central transition artifact MUST be consumed from the typed
# WriteReceipt->CertifiedTreeTransition producer, not re-derived by diffing".
# The live producer is the typed WriteReceipt->CertifiedTreeTransition function.
_MUST_TRANSITION_FROM_RECEIPT = MustClause(
    must_id="PIPE-MUST-10",
    spec_source=_C,
    excerpt=(
        "The central transition artifact MUST be consumed from the typed "
        "WriteReceipt→CertifiedTreeTransition producer, not re-derived by diffing "
        "materialized state."
    ),
    mapping_kind="writer_refusal",
    target_ref=(
        "lawvm.core.certified_transition:certified_tree_transitions_from_receipt"
    ),
)

# §7 / §11.7 — "legal authority — what courts/official publishers decide; LawVM
# MUST NOT claim this." A declared non-guarantee (a contract boundary, not an
# enforced runtime check): LawVM does not claim legal/official authority.
_MUST_NO_LEGAL_AUTHORITY = MustClause(
    must_id="PIPE-MUST-11",
    spec_source=_C,
    excerpt=(
        "**legal authority** — what courts/official publishers decide; LawVM MUST "
        "NOT claim this."
    ),
    mapping_kind="declared_non_guarantee",
    target_ref="reference_classification_is_surface_not_replay_authority",
)

# §6 / §11.5 — "A registered blocking finding MUST have production liveness (§6)."
# Same live gate as the §6 MUSTs (guard-liveness totality).
_MUST_BLOCKING_FINDING_LIVENESS = MustClause(
    must_id="PIPE-MUST-12",
    spec_source=_C,
    excerpt="A registered blocking finding MUST have production liveness (§6).",
    mapping_kind="checker_step",
    target_ref="tests.test_guard_liveness_totality",
)

#: The v0 curated MUST-trace ledger rows.
V0_MUST_CLAUSES: tuple[MustClause, ...] = (
    _MUST_WAIST_STAGERESULT,
    _MUST_ACCOUNTING_PARTITION,
    _MUST_UNOWNED_VIOLATION_ZERO,
    _MUST_BLOCKING_CODE_DRILLED,
    _MUST_FIRE_DRILL_DRIVES_PRODUCTION,
    _MUST_REGISTRY_PRODUCER_RECONCILED,
    _MUST_CONFIDENCE_NOT_CONTROL,
    _MUST_PROJECTION_NO_AUTHORITY,
    _MUST_CORRECTED_BYTES_REBOUND,
    _MUST_TRANSITION_FROM_RECEIPT,
    _MUST_NO_LEGAL_AUTHORITY,
    _MUST_BLOCKING_FINDING_LIVENESS,
)


def v0_must_trace_ledger() -> MustTraceLedger:
    """The v0 :class:`MustTraceLedger` over the declared in-scope spec files."""
    return MustTraceLedger(
        V0_MUST_CLAUSES,
        must_trace_version=MUST_TRACE_VERSION,
        in_scope_files=MUST_TRACE_V0_IN_SCOPE_FILES,
    )


__all__ = [
    "ENFORCED_MAPPING_KINDS",
    "MAPPING_KINDS",
    "MUST_TRACE_V0_IN_SCOPE_FILES",
    "MUST_TRACE_VERSION",
    "MappingKind",
    "MustClause",
    "MustTraceError",
    "MustTraceLedger",
    "V0_MUST_CLAUSES",
    "v0_must_trace_ledger",
]
