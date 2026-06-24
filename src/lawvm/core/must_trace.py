"""``lawvm.must_trace`` — the MUST-trace ledger + drift detector (Pro §13 step 5).

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
  (:data:`MUST_TRACE_IN_SCOPE_FILES` — at v2, THREE files: the pipeline
  contract, the provision-state seam contract, and the certified
  tree-transition trace spec), NOT all 66 ``notes/*.md`` and NOT source
  docstrings. Expanding the scope is a version bump, exactly like
  :data:`~lawvm.core.claim_surface_manifest.CLAIM_SURFACE_VERSION`. Each newly
  in-scope spec's MUSTs become individually accountable — that is the
  compounding payoff of widening scope.
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
MUST_TRACE_VERSION = "v2"

#: The DECLARED in-scope spec-file subset (paths relative to repo root). The
#: linter ranges over exactly these files — NOT all of ``notes/*.md`` and NOT
#: source docstrings. The scope has widened by version:
#:
#: * v0: the pipeline contract alone.
#: * v1: ALSO the provision-state seam contract — every MUST of the public
#:   consumer-facing seam became individually accountable.
#: * v2: ALSO the certified tree-transition trace spec — the CORE proof-carrying
#:   artifact a certificate checker replays (19 normative MUSTs), so every MUST
#:   of the replayable patch grammar becomes individually accountable.
#:
#: The ``MUST_TRACE_V1_IN_SCOPE_FILES`` name is retained for back-compatibility;
#: :data:`MUST_TRACE_IN_SCOPE_FILES` is the current (v2) tuple.
MUST_TRACE_IN_SCOPE_FILES: tuple[str, ...] = (
    "notes/LAWVM_PIPELINE_CONTRACT.md",
    "notes/SEAM_SPEC_PROVISION_STATE.md",
    "notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md",
)

#: Back-compat alias for the in-scope tuple (was the v1 name).
MUST_TRACE_V1_IN_SCOPE_FILES: tuple[str, ...] = MUST_TRACE_IN_SCOPE_FILES

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
    in_scope_files: tuple[str, ...] = MUST_TRACE_IN_SCOPE_FILES

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
# The curated ledger (v0 pipeline + v1 seam + v2 cert-trace rows).             #
#                                                                              #
# Each row maps ONE normative MUST in an in-scope spec file to a REAL target.  #
# Where a MUST currently maps to NOTHING (e.g. a consumer-side behavioural     #
# obligation the seam cannot enforce), it is recorded as ``deferred_with_owner``#
# with a precise owner + reason — NEVER given a fake mapping. The drift        #
# detector (tests/test_must_trace.py) re-scans the files and asserts every     #
# normative MUST is represented here + every target resolves.                  #
# --------------------------------------------------------------------------- #

_C = "notes/LAWVM_PIPELINE_CONTRACT.md"
_S = "notes/SEAM_SPEC_PROVISION_STATE.md"

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

# --------------------------------------------------------------------------- #
# The seam contract MUSTs (notes/SEAM_SPEC_PROVISION_STATE.md, spec_version    #
# 0.3). The seam is the public, consumer-facing provision-state query surface. #
# Its normative MUSTs split cleanly: ENGINE-SIDE requirements LawVM verifies    #
# (address/query validation, hash field exclusion, fail-loud resolution) map   #
# to live checker steps; CONSUMER/MAINTAINER behavioural obligations the seam   #
# CANNOT enforce (pin spec_version, rerun canaries, never key on content_hash  #
# alone) are honest deferred_with_owner findings naming the change-detector     #
# regression suite as the detection backstop, not the enforcement.             #
# --------------------------------------------------------------------------- #

# §1 — "The string MUST NOT contain whitespace around segment separators, kind
# names, or labels". Enforced: the selector diagnostic emits invalid_address
# (with a canonical suggestion) for non-canonical selector whitespace.
_MUST_SEAM_SELECTOR_NO_WHITESPACE = MustClause(
    must_id="SEAM-MUST-01",
    spec_source=_S,
    excerpt=(
        "The string MUST NOT contain whitespace around segment separators, kind "
        "names, or labels; non-canonical selector whitespace is `invalid_address` "
        "with a canonical suggestion."
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.provision_state:provision_selector_diagnostic",
)

# §1 — "`as_of` ...; MUST be non-empty". Enforced: the query-date validator
# rejects an empty as_of (-> invalid_query, no replay run).
_MUST_SEAM_AS_OF_NON_EMPTY = MustClause(
    must_id="SEAM-MUST-02",
    spec_source=_S,
    excerpt="MUST be non-empty and",
    mapping_kind="checker_step",
    target_ref="lawvm.core.timeline_selection:_validate_query_date",
)

# §1 — "MUST NOT contain leading or trailing whitespace" (the as_of date).
# Same enforcement: the query-date validator rejects whitespace-tainted as_of.
_MUST_SEAM_AS_OF_NO_WHITESPACE = MustClause(
    must_id="SEAM-MUST-03",
    spec_source=_S,
    excerpt="MUST NOT contain leading or trailing whitespace.",
    mapping_kind="checker_step",
    target_ref="lawvm.core.timeline_selection:_validate_query_date",
)

# §2.1 — "Consumers MUST treat any status other than `selected` as 'no asserted
# text-state'." A consumer-side behavioural obligation: the seam emits the
# typed non-selected statuses but cannot enforce how a consumer reads them.
_MUST_SEAM_CONSUMER_NON_SELECTED = MustClause(
    must_id="SEAM-MUST-04",
    spec_source=_S,
    excerpt=(
        'Consumers MUST treat any status other than `selected` as "no asserted '
        'text-state".'
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers (e.g. MeVM); reason=this is a consumer "
        "READING obligation LawVM cannot enforce in the consumer's process — the "
        "seam emits the typed closed status set (build_provision_state_response) "
        "and refuses to mint text on non-selected, but no LawVM check binds how a "
        "consumer interprets a non-selected status"
    ),
)

# §2.1 — "A near-miss address MUST be expected to fail rather than resolve to a
# different provision." Enforced: resolution is exact-match then UNIQUE-suffix
# only, never arbitrary order / fuzzy fallback (resolve_address).
_MUST_SEAM_NEAR_MISS_FAILS = MustClause(
    must_id="SEAM-MUST-05",
    spec_source=_S,
    excerpt=(
        "A near-miss address\nMUST be expected to fail rather than resolve to a "
        "different provision."
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.provision_state:resolve_address",
)

# §3.1 — "A change in engine build, git commit, working-tree dirtiness, or
# non-control diagnostic/proof metadata MUST NOT, by itself, change
# `derived_state_hash`." Enforced: _hash_payload feeds ONLY the §3 field set
# into the canonical hash input; engine/diagnostics/source_locator are excluded.
_MUST_SEAM_ENGINE_NOT_HASHED = MustClause(
    must_id="SEAM-MUST-06",
    spec_source=_S,
    excerpt=(
        "A change in engine build, git commit, working-tree dirtiness, or "
        "non-control diagnostic/proof metadata MUST NOT, by itself, change "
        "`derived_state_hash`."
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.provision_state:_hash_payload",
)

# §3.3 — "Consumers MAY rely on full-hash equality as text-state identity but
# MUST NOT rely on `content_hash` alone." Consumer-side keying obligation; the
# seam exposes both hashes but cannot enforce which a consumer keys on.
_MUST_SEAM_NOT_CONTENT_HASH_ALONE_33 = MustClause(
    must_id="SEAM-MUST-07",
    spec_source=_S,
    excerpt=(
        "Consumers MAY rely on full-hash\nequality as text-state identity but MUST "
        "NOT rely on `content_hash` alone."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer KEYING choice LawVM "
        "cannot enforce — the seam emits both content_hash (text-only, structure-"
        "blind, §3.3) and the full derived_state_hash, but no LawVM check forbids "
        "a consumer from keying decisions on content_hash alone"
    ),
)

# §3.4 — "Consumers MUST pin `spec_version` ...". Consumer-side pinning
# obligation; LawVM ships the spec_version marker + the regression suite as a
# detector, not as enforcement.
_MUST_SEAM_PIN_SPEC_VERSION_34 = MustClause(
    must_id="SEAM-MUST-08",
    spec_source=_S,
    excerpt="Consumers MUST pin\n`spec_version` and",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer PINNING obligation "
        "LawVM cannot enforce — the response exposes spec_version (excluded from "
        "the hash) and the corpus-backed change-detector "
        "tests/test_fi_provision_state_consumer_contract.py surfaces semantic hash "
        "drift, but neither forces a consumer to pin the version it validated"
    ),
)

# §3.4 — "... MUST NOT assume hash stability across engine versions." Consumer-
# side cross-engine-stability boundary the seam declares but cannot enforce.
_MUST_SEAM_NO_CROSS_ENGINE_STABILITY = MustClause(
    must_id="SEAM-MUST-09",
    spec_source=_S,
    excerpt="MUST NOT assume hash stability across engine versions.",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer ASSUMPTION the seam "
        "cannot enforce — cross-engine hash stability is an explicit NON-guarantee "
        "(§3.4); the seam may change a hash when selection/eligibility semantics "
        "change, and no LawVM check forbids a consumer from assuming otherwise"
    ),
)

# §6.2 — "Consumers that key decisions on text-state identity MUST use the full
# `derived_state_hash`, never `content_hash` alone." Consumer-side keying
# obligation (a restatement of §3.3 in the limitations section).
_MUST_SEAM_NOT_CONTENT_HASH_ALONE_62 = MustClause(
    must_id="SEAM-MUST-10",
    spec_source=_S,
    excerpt=(
        "Consumers that key decisions on text-state identity\nMUST use the full "
        "`derived_state_hash`, never `content_hash` alone."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=same un-enforceable consumer "
        "keying obligation as SEAM-MUST-06 (§3.3), restated in the §6.2 known-"
        "limitations section — the seam emits both hashes; it cannot constrain "
        "which a consumer keys on"
    ),
)

# §7 — "A change is breaking — and MUST bump `spec_version` — iff it changes
# any of: [the hash field set, content_hash def, status enum, eligibility
# predicate, resolution rule]." A maintainer change-process obligation.
_MUST_SEAM_BREAKING_BUMPS_VERSION = MustClause(
    must_id="SEAM-MUST-11",
    spec_source=_S,
    excerpt=(
        "A change is **breaking** — and MUST\nbump `spec_version` — iff it changes "
        "any of:"
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=LawVM seam maintainers (provision-state contract owners); "
        "reason=a human change-process obligation (bump spec_version on a breaking "
        "change) — the change-detector "
        "tests/test_fi_provision_state_consumer_contract.py FLAGS divergence in the "
        "hashed field set, but no automated gate asserts a corresponding "
        "spec_version bump was made"
    ),
)

# §7 — "Breaking changes are announced via a `spec_version` bump ... Consumers
# MUST pin the `spec_version` they validated against." Consumer-side pinning
# obligation (the §7 restatement of §3.4).
_MUST_SEAM_PIN_SPEC_VERSION_7 = MustClause(
    must_id="SEAM-MUST-12",
    spec_source=_S,
    excerpt=(
        "consumers SHOULD also run in their own CI. Consumers MUST pin the\n"
        "`spec_version` they validated against."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=same un-enforceable consumer "
        "pinning obligation as SEAM-MUST-07 (§3.4), restated in §7 — the seam "
        "publishes spec_version + the regression suite, but cannot force a "
        "consumer to pin the version it validated against"
    ),
)

# §7.2 — "Consumers that pin hashes on blocked fixed-term rows MUST rerun their
# canaries." Consumer-side re-verification obligation after a diagnostic-code
# refinement that changes the hashed expiry block.
_MUST_SEAM_RERUN_CANARIES_72 = MustClause(
    must_id="SEAM-MUST-13",
    spec_source=_S,
    excerpt="fixed-term rows MUST rerun their canaries.",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer RE-VERIFICATION "
        "obligation LawVM cannot enforce — a §7.2 diagnostic-code refinement "
        "changes derived_state_hash for blocked fixed-term rows; the seam cannot "
        "compel a consumer to rerun its own pinned canaries"
    ),
)

# §7.2 — "pinned consumers MUST still treat it as a semantic output change for
# the affected rows, covered by canary diffs." Consumer-side treatment
# obligation after the expiry-recognizer classification correction.
_MUST_SEAM_TREAT_AS_SEMANTIC_72 = MustClause(
    must_id="SEAM-MUST-14",
    spec_source=_S,
    excerpt=(
        "not a seam-schema change; pinned consumers MUST still treat it as a\n"
        "semantic output change for the affected rows, covered by canary diffs."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer TREATMENT obligation "
        "LawVM cannot enforce — the §7.2 expiry-recognizer correction changes "
        "status/hash for affected rows; the canary diffs in "
        "tests/test_fi_provision_state_consumer_contract.py surface it, but the "
        "seam cannot force a consumer to treat it as a semantic change"
    ),
)

# §7.3 — "consumers that enumerate statuses exhaustively or pin hashes on rows
# of break-carrying statutes MUST treat this as a semantic output change for
# those rows (canary diffs)." Consumer-side treatment obligation for the
# timeline-integrity status/hash widening.
_MUST_SEAM_TREAT_AS_SEMANTIC_73 = MustClause(
    must_id="SEAM-MUST-15",
    spec_source=_S,
    excerpt=(
        "consumers that enumerate statuses\nexhaustively or pin hashes on rows of "
        "break-carrying statutes MUST treat this\nas a semantic output change for "
        "those rows (canary diffs), exactly like the\n7.2 recognizer corrections."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream seam consumers; reason=a consumer TREATMENT obligation "
        "LawVM cannot enforce — the §7.3 timeline-integrity surfacing widens the "
        "status enum and hashed member set for break-carrying rows; the seam "
        "cannot compel a consumer to treat the change as semantic"
    ),
)

# --------------------------------------------------------------------------- #
# The certified tree-transition trace MUSTs                                     #
# (notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md, schema                          #
# lawvm.certified_tree_transition_trace.v0, spec_version 0.3). This is the CORE #
# proof-carrying artifact a certificate checker REPLAYS. v2 brings it in scope. #
#                                                                               #
# The MUSTs split into three honest buckets:                                    #
#                                                                               #
# * REPLAY / HASH / ORDERING obligations the writer self-check enforces on      #
#   every emitted bundle map to ``checker_step`` targets in                     #
#   lawvm.tools.certificate_bundle (verify_bundle folds the trace, recomputes   #
#   every committed root, and re-asserts the per-action preconditions; the      #
#   anchor-or-residual rule is enforced inside build_certificate_bundle).       #
#   HONESTY: verify_bundle is the WRITER-SIDE self-check, NOT the independent    #
#   zero-knowledge checker v0 — the spec states that checker is not yet         #
#   implemented. A ``checker_step`` here asserts a LIVE gate verifies the MUST   #
#   on emitted bundles, not that the standalone re-implementing checker exists.  #
# * DISPLAY / CONSUMER / DESIGN-DISCIPLINE obligations (derived-labelling, the   #
#   attrs non-semantic rule, "don't conflate the two hashes", "don't present    #
#   annotation as certified") that no live LawVM gate enforces are honest       #
#   ``deferred_with_owner`` findings naming the owner + reason.                  #
#                                                                               #
# No cert-trace MUST maps to ``invariant_id`` (V0_INVARIANTS are FI/domain      #
# claim invariants, none about the trace artifact) or ``declared_non_guarantee``#
# (no AssumptionRegister / ClaimSpec handle names the trace's legal-meaning     #
# boundary) — inventing such a mapping would be a fake target, the cardinal sin.#
# --------------------------------------------------------------------------- #

_T = "notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md"

# §1 — "Finer-grained change attribution ... is DERIVED ... and MUST be labelled
# as derived, never presented as certification." A viewer/report labelling
# obligation: LawVM cannot enforce how a downstream renderer labels derived
# diffs.
_MUST_TRACE_DERIVED_LABELLED = MustClause(
    must_id="TRACE-MUST-01",
    spec_source=_T,
    excerpt=(
        "DERIVED by diffing certified pre/post subtrees and MUST be labelled as "
        "derived, never presented as certification."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream viewers/reports (e.g. statute-timeline viewer); "
        "reason=a RENDERING-labelling obligation LawVM cannot enforce in the "
        "consumer's process — finer-grained per-subsection diffs are derived from "
        "certified pre/post subtrees, but no LawVM check binds a viewer to label "
        "them 'derived' rather than presenting them as certification"
    ),
)

# §2.2 — "Consumers MUST NOT key decisions on attrs via this hash" (the
# structural hash is attrs-blind). A consumer keying obligation the artifact
# family cannot enforce in the consumer.
_MUST_TRACE_ATTRS_NOT_KEYED = MustClause(
    must_id="TRACE-MUST-02",
    spec_source=_T,
    excerpt="Consumers MUST NOT key decisions on attrs via this hash",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream trace/bundle consumers; reason=a consumer KEYING "
        "obligation LawVM cannot enforce — the §2.2 structural hash is attrs-blind "
        "(two subtrees differing only in attrs collide); no LawVM check forbids a "
        "consumer from keying a decision on attrs through this hash"
    ),
)

# §2.2 — attrs non-semantic rule: "Such state MUST be represented in the typed
# node shape ... or projection payloads ...". A drafting/design discipline on
# the node schema with no live cross-tree lint asserting no semantic field lives
# only in attrs.
_MUST_TRACE_ATTRS_NON_SEMANTIC = MustClause(
    must_id="TRACE-MUST-03",
    spec_source=_T,
    excerpt="Such state MUST be represented in the typed node shape",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm IR node-shape designers (the attrs-blind structural-hash "
        "soundness precondition); reason=a DESIGN-discipline obligation — no field "
        "affecting text-state/applicability/eligibility/status/addressability/"
        "source-identity may live ONLY in attrs; no live gate scans node schemas "
        "to assert no such semantic field is attrs-only, so the precondition of "
        "attrs-blindness is unenforced, tracked"
    ),
)

# §2.2 — conditional future obligation: "If the engine ever needs a semantic
# field that only fits attrs ... attrs MUST be folded into the structural hash
# under a schema bump." A maintainer change-process obligation conditioned on a
# future event.
_MUST_TRACE_ATTRS_FOLD_ON_BUMP = MustClause(
    must_id="TRACE-MUST-04",
    spec_source=_T,
    excerpt="`attrs` MUST be folded into the structural hash under a schema bump.",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm trace-schema maintainers; reason=a CONDITIONAL future "
        "change-process obligation — should the engine ever require a semantic "
        "field that only fits attrs, the frozen §2.2 hash recipe is unsound and "
        "attrs must be folded in under a schema bump; no live gate detects that "
        "trigger condition, so it is an unenforced maintainer obligation"
    ),
)

# §2.2 — "Distinct from the text-only content_hash of seam responses ...; the
# two MUST NOT be conflated even though both are sha256 hex values." A
# discipline against conflating the structural hash with the seam content_hash.
_MUST_TRACE_HASHES_NOT_CONFLATED = MustClause(
    must_id="TRACE-MUST-05",
    spec_source=_T,
    excerpt=(
        "the two MUST NOT be conflated even though\n  both are sha256 hex values."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream trace/seam consumers; reason=a discipline LawVM cannot "
        "enforce in the consumer — the §2.2 structural hash and the seam's "
        "text-only content_hash are both sha256 hex but distinct values; no LawVM "
        "check forbids a consumer from conflating them"
    ),
)

# §3 — "Every content_hash MUST resolve in content_blobs (§4)." Enforced: the
# writer self-check folds the base tree + transitions and requires every
# referenced content_hash to have a blob row (TRACE.PAYLOAD_BLOB_MISSING).
_MUST_TRACE_BASE_CONTENT_HASH_RESOLVES = MustClause(
    must_id="TRACE-MUST-06",
    spec_source=_T,
    excerpt="Every `content_hash` MUST resolve in `content_blobs`",
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §4 — "Every hash referenced by the base tree, by any transition payload_hash,
# or by any checkpoint's active set MUST have a blob row
# (TRACE.PAYLOAD_BLOB_MISSING)." Enforced: verify_bundle asserts the payload
# blob exists for every set_subtree while folding the trace.
_MUST_TRACE_PAYLOAD_BLOB_PRESENT = MustClause(
    must_id="TRACE-MUST-07",
    spec_source=_T,
    excerpt="MUST have a blob row\n  (`TRACE.PAYLOAD_BLOB_MISSING`).",
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §5.1 — "Annotation drift never invalidates a trace; annotation MUST NOT be
# treated as certified content." A consumer/viewer obligation: display
# annotation is excluded from the hash, but LawVM cannot enforce how a consumer
# treats it.
_MUST_TRACE_ANNOTATION_NOT_CERTIFIED = MustClause(
    must_id="TRACE-MUST-08",
    spec_source=_T,
    excerpt="annotation MUST NOT be treated as certified content.",
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream trace consumers; reason=a consumer TREATMENT obligation "
        "LawVM cannot enforce — §5.1 display annotation (legal_op_summary, "
        "preparatory_refs, flags, ...) is excluded from transition_hash by "
        "construction, but no LawVM check binds a consumer to not treat it as "
        "certified content"
    ),
)

# §5.1 — "A viewer or report MUST NOT present preparatory_refs or other
# annotation as certified provenance unless it is also committed through a
# rooted projection family." A viewer-side presentation obligation.
_MUST_TRACE_VIEWER_NO_CERTIFIED_ANNOTATION = MustClause(
    must_id="TRACE-MUST-09",
    spec_source=_T,
    excerpt=(
        "A\nviewer or report MUST NOT present `preparatory_refs` or other "
        "annotation as\ncertified provenance"
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=downstream viewers/reports; reason=a PRESENTATION obligation LawVM "
        "cannot enforce in the consumer — non-hashed annotation may be shown as "
        "certified provenance only if also committed through a rooted projection "
        "family (e.g. transition-graph projection rows); no LawVM check binds a "
        "viewer to that condition"
    ),
)

# §5.1 — "effective_date is an ISO date, non-decreasing with sequence, and MUST
# be a member of the certificate's committed change-date set
# (TRACE.CHANGE_DATE_UNDECLARED)." Enforced: verify_bundle re-asserts ordering +
# change-date membership while folding the trace.
_MUST_TRACE_EFFECTIVE_DATE_DECLARED = MustClause(
    must_id="TRACE-MUST-10",
    spec_source=_T,
    excerpt=(
        "`effective_date` is an ISO date, non-decreasing with `sequence`, and "
        "MUST\n  be a member of the certificate's committed change-date set"
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §5.1 — "The base sentinel 0000-00-00 is not a change date and MUST NOT appear."
# No live gate rejects the sentinel literal specifically in a transition row;
# the writer never emits it (effective_date comes from real change dates) but
# that is by construction, not a checked refusal.
_MUST_TRACE_NO_BASE_SENTINEL = MustClause(
    must_id="TRACE-MUST-11",
    spec_source=_T,
    excerpt=(
        "The base sentinel `0000-00-00` is not a\n  change date and MUST NOT "
        "appear."
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm.tools.certificate_bundle (trace emitter / checker); "
        "reason=no live gate specifically rejects the 0000-00-00 base sentinel "
        "appearing as a transition effective_date — verify_bundle enforces "
        "change-date MEMBERSHIP (which the sentinel fails) but does not name the "
        "sentinel as a distinct refusal; the writer never emits it by "
        "construction, so the explicit MUST-NOT-appear rule is unenforced, tracked"
    ),
)

# §5.1 — "source_anchors ... Certified anchor spans are BYTE-LEVEL only:
# span_unit MUST be \"byte\" ...". Enforced: build_certificate_bundle verifies
# every certified anchor byte-level against the bundled raw source bytes (byte
# span + quote_hash) before it is carried; non-byte/failed anchors are dropped.
_MUST_TRACE_ANCHOR_SPAN_BYTE = MustClause(
    must_id="TRACE-MUST-12",
    spec_source=_T,
    excerpt='`span_unit` MUST be `"byte"`',
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:build_certificate_bundle",
)

# §5.1 — "When no anchor covers a source_ref ... a kind=source_anchor_unavailable
# residual scoped to this transition MUST exist in the certificate's residual
# ledger (§7)." Enforced: build_certificate_bundle emits a
# source_anchor_unavailable residual for every (transition, ref) without a
# re-verified byte anchor.
_MUST_TRACE_UNAVAILABLE_RESIDUAL = MustClause(
    must_id="TRACE-MUST-13",
    spec_source=_T,
    excerpt=(
        "`kind=source_anchor_unavailable` residual scoped to this transition "
        "MUST\n  exist in the certificate's residual ledger"
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:build_certificate_bundle",
)

# §6.1 — Reserved action names "are set aside for future spec versions and MUST
# NOT be emitted under v0." Enforced (producer side): the producer ONLY emits
# set_subtree / delete_subtree — it cannot construct a reserved action.
_MUST_TRACE_RESERVED_NOT_EMITTED = MustClause(
    must_id="TRACE-MUST-14",
    spec_source=_T,
    excerpt=(
        "are set aside for future spec versions and MUST NOT be\nemitted under v0."
    ),
    mapping_kind="writer_refusal",
    target_ref=(
        "lawvm.core.certified_transition:certified_tree_transitions_from_receipt"
    ),
)

# §6.1 — "A checker encountering a reserved or unknown action returns
# TRACE.UNKNOWN_ACTION → INVALID; it MUST NOT guess semantics." Enforced:
# verify_bundle raises BundleSelfCheckError on any action outside
# {set_subtree, delete_subtree} rather than guessing.
_MUST_TRACE_NO_GUESS_ACTION = MustClause(
    must_id="TRACE-MUST-15",
    spec_source=_T,
    excerpt="it MUST NOT guess semantics.",
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §7 — "Every source_ref of every transition MUST satisfy ONE of: [a verified
# byte anchor] or [a source_anchor_unavailable residual]." Enforced:
# build_certificate_bundle either carries a re-verified byte anchor or emits the
# fail-loud source_anchor_unavailable residual for every ref.
_MUST_TRACE_ANCHOR_OR_RESIDUAL = MustClause(
    must_id="TRACE-MUST-16",
    spec_source=_T,
    excerpt="Every `source_ref` of every transition MUST satisfy ONE of:",
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:build_certificate_bundle",
)

# §8.2 — "after folding all transitions with effective_date <= date, the
# recomputed covering-state hash MUST equal tree_hash (TRACE.CHECKPOINT_MISMATCH)."
# Enforced: verify_bundle folds the batch and re-asserts the §8.1 covering-state
# hash against each committed checkpoint.
_MUST_TRACE_CHECKPOINT_MATCH = MustClause(
    must_id="TRACE-MUST-17",
    spec_source=_T,
    excerpt=(
        "the recomputed covering-state hash MUST equal\n  `tree_hash` "
        "(`TRACE.CHECKPOINT_MISMATCH`)."
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §8.2 — "the row dates MUST equal the change-date set committed under
# change_dates_root ...". Enforced: verify_bundle recomputes change_dates_root
# from the checkpoint dates and re-asserts it against the committed time_axis.
_MUST_TRACE_CHECKPOINT_DATES_EQUAL = MustClause(
    must_id="TRACE-MUST-18",
    spec_source=_T,
    excerpt=(
        "the row dates MUST equal the change-date set committed under\n"
        "`change_dates_root`"
    ),
    mapping_kind="checker_step",
    target_ref="lawvm.tools.certificate_bundle:verify_bundle",
)

# §9 — Failure model: "each maps the bundle to verdict INVALID and MUST embed
# the offending row identity ... and enough row content to be self-evidencing."
# A property of the INDEPENDENT checker v0's typed failures; that checker is not
# yet implemented (the writer self-check raises BundleSelfCheckError with the
# offending row id but is not the spec's verdict-producing checker).
_MUST_TRACE_FAILURES_SELF_EVIDENCING = MustClause(
    must_id="TRACE-MUST-19",
    spec_source=_T,
    excerpt=(
        "MUST embed the offending row identity (transition_id / content_hash / "
        "date) and\nenough row content to be self-evidencing"
    ),
    mapping_kind="deferred_with_owner",
    target_ref=(
        "owner=lawvm certificate checker v0 (not yet implemented; the spec header "
        "states the trace-folding checker is unimplemented); reason=the §9 typed "
        "TRACE.* failures that map a bundle to verdict INVALID and embed the "
        "offending row identity are properties of the verdict-producing checker; "
        "verify_bundle is the writer-side self-check (raises with the offending "
        "transition_id but produces no verdict), so the self-evidencing typed-"
        "failure contract is unenforced until checker v0 exists, tracked"
    ),
)

#: The v2 cert-trace MUST rows (one per normative MUST token of
#: notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md, less the single waived RFC-2119
#: notation sentence).
_TRACE_MUST_CLAUSES: tuple[MustClause, ...] = (
    _MUST_TRACE_DERIVED_LABELLED,
    _MUST_TRACE_ATTRS_NOT_KEYED,
    _MUST_TRACE_ATTRS_NON_SEMANTIC,
    _MUST_TRACE_ATTRS_FOLD_ON_BUMP,
    _MUST_TRACE_HASHES_NOT_CONFLATED,
    _MUST_TRACE_BASE_CONTENT_HASH_RESOLVES,
    _MUST_TRACE_PAYLOAD_BLOB_PRESENT,
    _MUST_TRACE_ANNOTATION_NOT_CERTIFIED,
    _MUST_TRACE_VIEWER_NO_CERTIFIED_ANNOTATION,
    _MUST_TRACE_EFFECTIVE_DATE_DECLARED,
    _MUST_TRACE_NO_BASE_SENTINEL,
    _MUST_TRACE_ANCHOR_SPAN_BYTE,
    _MUST_TRACE_UNAVAILABLE_RESIDUAL,
    _MUST_TRACE_RESERVED_NOT_EMITTED,
    _MUST_TRACE_NO_GUESS_ACTION,
    _MUST_TRACE_ANCHOR_OR_RESIDUAL,
    _MUST_TRACE_CHECKPOINT_MATCH,
    _MUST_TRACE_CHECKPOINT_DATES_EQUAL,
    _MUST_TRACE_FAILURES_SELF_EVIDENCING,
)


#: The v2 curated MUST-trace ledger rows (pipeline contract + provision-state
#: seam contract + certified tree-transition trace). The first 12 are the v0
#: pipeline-contract rows; the 15 SEAM-MUST-* rows are the v1 widening onto the
#: seam contract; the 19 TRACE-MUST-* rows are the v2 widening onto the
#: certified tree-transition trace (one per normative MUST token of its spec,
#: less the single waived RFC-2119 notation sentence). The ``V1_*`` name is
#: retained for back-compatibility; :data:`MUST_CLAUSES` is the current set.
MUST_CLAUSES: tuple[MustClause, ...] = (
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
    _MUST_SEAM_SELECTOR_NO_WHITESPACE,
    _MUST_SEAM_AS_OF_NON_EMPTY,
    _MUST_SEAM_AS_OF_NO_WHITESPACE,
    _MUST_SEAM_CONSUMER_NON_SELECTED,
    _MUST_SEAM_NEAR_MISS_FAILS,
    _MUST_SEAM_ENGINE_NOT_HASHED,
    _MUST_SEAM_NOT_CONTENT_HASH_ALONE_33,
    _MUST_SEAM_PIN_SPEC_VERSION_34,
    _MUST_SEAM_NO_CROSS_ENGINE_STABILITY,
    _MUST_SEAM_NOT_CONTENT_HASH_ALONE_62,
    _MUST_SEAM_BREAKING_BUMPS_VERSION,
    _MUST_SEAM_PIN_SPEC_VERSION_7,
    _MUST_SEAM_RERUN_CANARIES_72,
    _MUST_SEAM_TREAT_AS_SEMANTIC_72,
    _MUST_SEAM_TREAT_AS_SEMANTIC_73,
    *_TRACE_MUST_CLAUSES,
)

#: Back-compat alias for the curated ledger rows (was the v1 name). Now carries
#: the v2 set (pipeline + seam + cert-trace).
V1_MUST_CLAUSES: tuple[MustClause, ...] = MUST_CLAUSES


def must_trace_ledger() -> MustTraceLedger:
    """The current :class:`MustTraceLedger` over the declared in-scope spec files."""
    return MustTraceLedger(
        MUST_CLAUSES,
        must_trace_version=MUST_TRACE_VERSION,
        in_scope_files=MUST_TRACE_IN_SCOPE_FILES,
    )


#: Back-compat alias for the ledger constructor (was the v1 name).
v1_must_trace_ledger = must_trace_ledger


__all__ = [
    "ENFORCED_MAPPING_KINDS",
    "MAPPING_KINDS",
    "MUST_CLAUSES",
    "MUST_TRACE_IN_SCOPE_FILES",
    "MUST_TRACE_V1_IN_SCOPE_FILES",
    "MUST_TRACE_VERSION",
    "MappingKind",
    "MustClause",
    "MustTraceError",
    "MustTraceLedger",
    "V1_MUST_CLAUSES",
    "must_trace_ledger",
    "v1_must_trace_ledger",
]
