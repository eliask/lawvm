"""``lawvm.invariant_spec.v0`` — machine-readable invariant rows + the bucket taxonomy.

WHAT THIS ENABLES. An :class:`InvariantSpec` is one machine-readable row of the
total-invariant-mining registry (Pro doc §13 step 3): it names a claim it serves
(``claim_id``), where it lives (``plane`` / ``waist`` / ``unit_kind``), what it
asserts (``predicate``), how it is held to account (``checker_ref`` /
``finding_code`` / ``root_membership``), and — the load-bearing field — which
terminal ``bucket`` the invariant candidate ends in.

THE BUCKET TAXONOMY (Pro doc §4, the forbidden-bucket coverage rule). Every
invariant candidate must terminate in exactly ONE of nine ALLOWED buckets. The
single FORBIDDEN bucket is ``implicit_convention`` — an invariant that is merely
assumed to hold by custom, with no live check, no refusal, no declared residual,
no owner. The coverage gate (see :mod:`tests.test_claim_surface_coverage`) is
exactly: every declared claim has ≥1 invariant in an ALLOWED bucket, and ZERO
invariants sit in ``implicit_convention``. That is "no public claim without a
live accounting path" made executable.

BINDING, NOT FORKING. This module does NOT fork a parallel registry. It
cross-references the prose audit registry (``notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md``,
already InvariantSpec-shaped: id/plane/waist/unit/predicate/status/finding-code)
via the row id carried in ``audit_registry_ref``, and it cites real production
finding codes from :mod:`lawvm.core.observation_registry` where one exists. The
v0 rows below are the live accounting paths for the v0 ClaimSurfaceManifest.

HONESTY BOUNDARY (Pro §12 — completeness is claim-relative and versioned). An
:class:`InvariantSet` carries an ``(spec_version, claim_surface_version)`` pair.
v0 binds to a DECLARED SUBSET of claims; it does NOT auto-generate invariants
from the finite-axis generator (planes × waists × ...), does NOT run fire-drills
(Pro §13 step 4), and does NOT run the MUST-trace linter (step 5). "All
invariants" is never asserted — only "all required invariants for THIS declared
claim surface at THIS version".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, map_root

_SCHEMA_INVARIANT_SPEC = "lawvm.invariant_spec.v0"
_SCHEMA_INVARIANT_SET = "lawvm.invariant_set.v0"
_DOMAIN_INVARIANT_SPEC = "invariant_spec"
_DOMAIN_INVARIANT_SET_ROOT = "invariant_set"

# The invariant-spec schema version (Pro §12 ``InvariantSet(spec_version, ...)``).
INVARIANT_SPEC_VERSION = "v0"

# --------------------------------------------------------------------------- #
# The terminal bucket taxonomy (Pro doc §4).                                   #
# --------------------------------------------------------------------------- #

# The nine ALLOWED terminal buckets. An invariant in any of these has a LIVE
# accounting path (a check / refusal / verifier / fixture) or an EXPLICIT
# residual / non-guarantee / deferral / out-of-claim ruling — never an unowned
# assumption.
Bucket = Literal[
    "implemented_check",        # a live check fires from production
    "writer_refusal",           # a writer/constructor refuses the bad shape
    "checker_step",             # a step in the pack/dossier checker verifies it
    "projection_verifier",      # a projection-coverage verifier checks it
    "corruption_fixture_only",  # only exercised by a corruption fixture (drill)
    "declared_residual",        # a typed residual owns the gap (named, not silent)
    "declared_non_guarantee",   # an AssumptionRegister entry declares the boundary
    "deferred_with_owner",      # deferred, with a named owner + reason
    "rejected_not_in_claim",    # explicitly out of the claim surface
    # --- the single FORBIDDEN bucket (Pro §4) ---
    "implicit_convention",      # assumed by custom; NO live path — the enemy
]

#: The ALLOWED terminal buckets (a claim is covered iff ≥1 invariant lands here).
ALLOWED_BUCKETS: frozenset[str] = frozenset(
    {
        "implemented_check",
        "writer_refusal",
        "checker_step",
        "projection_verifier",
        "corruption_fixture_only",
        "declared_residual",
        "declared_non_guarantee",
        "deferred_with_owner",
        "rejected_not_in_claim",
    }
)

#: The single FORBIDDEN bucket — an invariant here is an unowned implicit
#: convention, the exact enemy the coverage gate forbids (Pro §4).
FORBIDDEN_BUCKET = "implicit_convention"

ALL_BUCKETS: frozenset[str] = ALLOWED_BUCKETS | {FORBIDDEN_BUCKET}

# The six planes (audit registry / Pro §13). NOT closed-validated against an
# enum here (the audit registry owns the canonical plane set) — carried as a
# free typed token so a new plane in a future generator pass is not rejected.
Plane = str

# Spec status (mirrors the audit-registry legend: IMPL / PART / OPEN /
# DEFER / IMPL-by-construction). Free typed token, cross-referenced to the
# registry row via ``audit_registry_ref``.
InvariantStatus = str


class InvariantSpecError(ValueError):
    """An invariant-spec object violates a v0 schema invariant."""


@dataclass(frozen=True, slots=True)
class InvariantSpec:
    """``lawvm.invariant_spec.v0`` — one machine-readable invariant row.

    Fields (Pro §13 step 3 schema):

    * ``id`` — stable invariant id (often the audit-registry row id, e.g.
      ``"SURF-05"``, or a new id for a backbone-only row).
    * ``claim_id`` — the :class:`~lawvm.core.claim_surface_manifest.ClaimSpec`
      this invariant serves (the gate's join key).
    * ``plane`` — the plane the invariant lives on (source/surface/legal_state/
      evidence/projection/firewall/certificate/cross_plane/declaration).
    * ``waist`` — the waist-edge / stage the invariant guards (free token).
    * ``unit_kind`` — what unit the invariant ranges over (per-unit / per-op /
      aggregate / static / sweep).
    * ``predicate`` — the prose assertion (what holds).
    * ``owner`` — the module/boundary that owns the accounting path.
    * ``checker_ref`` — a dotted ref to the checking code (module:symbol) or "".
    * ``finding_code`` — the production finding code emitted on violation, or "".
    * ``root_membership`` — the named root the invariant's universe/evidence is
      committed to (e.g. ``"universe_root"``), or "".
    * ``bucket`` — the terminal bucket (must be in :data:`ALL_BUCKETS`).
    * ``status`` — IMPL/PART/OPEN/DEFER cross-referenced to the audit registry.
    * ``audit_registry_ref`` — the registry row id this binds to (NOT a fork), or "".
    """

    id: str
    claim_id: str
    plane: Plane
    waist: str
    unit_kind: str
    predicate: str
    owner: str
    bucket: Bucket
    checker_ref: str = ""
    finding_code: str = ""
    root_membership: str = ""
    status: InvariantStatus = "OPEN"
    audit_registry_ref: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise InvariantSpecError("InvariantSpec.id must be a non-empty id")
        if not self.claim_id or not self.claim_id.strip():
            raise InvariantSpecError(
                f"InvariantSpec {self.id!r} must cite a non-empty claim_id "
                f"— an invariant serving no claim is not part of any claim surface"
            )
        if not self.predicate or not self.predicate.strip():
            raise InvariantSpecError(
                f"InvariantSpec {self.id!r} must carry a non-empty predicate"
            )
        if not self.owner or not self.owner.strip():
            raise InvariantSpecError(
                f"InvariantSpec {self.id!r} must name a non-empty owner"
            )
        if self.bucket not in ALL_BUCKETS:
            raise InvariantSpecError(
                f"InvariantSpec {self.id!r} bucket must be one of {sorted(ALL_BUCKETS)!r}, "
                f"got {self.bucket!r}"
            )
        for token_name, value in (
            ("plane", self.plane),
            ("waist", self.waist),
            ("unit_kind", self.unit_kind),
        ):
            if not value or not value.strip():
                raise InvariantSpecError(
                    f"InvariantSpec {self.id!r} {token_name} must be a non-empty token"
                )

    @property
    def is_allowed(self) -> bool:
        """True iff the invariant terminates in an ALLOWED bucket (a live path)."""
        return self.bucket in ALLOWED_BUCKETS

    @property
    def is_forbidden(self) -> bool:
        """True iff the invariant sits in the FORBIDDEN ``implicit_convention`` bucket."""
        return self.bucket == FORBIDDEN_BUCKET

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_INVARIANT_SPEC,
            "id": nfc(self.id),
            "claim_id": nfc(self.claim_id),
            "plane": nfc(self.plane),
            "waist": nfc(self.waist),
            "unit_kind": nfc(self.unit_kind),
            "predicate": nfc(self.predicate),
            "owner": nfc(self.owner),
            "bucket": self.bucket,
            "checker_ref": nfc(self.checker_ref),
            "finding_code": nfc(self.finding_code),
            "root_membership": nfc(self.root_membership),
            "status": nfc(self.status),
            "audit_registry_ref": nfc(self.audit_registry_ref),
        }

    @property
    def invariant_body_hash(self) -> str:
        return leaf_hash(_DOMAIN_INVARIANT_SPEC, self.to_canonical_dict())


@dataclass(frozen=True, slots=True)
class InvariantSet:
    """``lawvm.invariant_set.v0`` — the invariant rows for a claim surface version.

    Carries the ``(spec_version, claim_surface_version)`` pair (Pro §12):
    completeness is relative to a DECLARED claim surface at a version. The
    :attr:`invariant_set_root` is a ``MapRoot`` over ``{invariant_id: body_hash}``
    so adding/dropping/editing any invariant changes the root.
    """

    invariants: tuple[InvariantSpec, ...]
    spec_version: str = INVARIANT_SPEC_VERSION
    claim_surface_version: str = "v0"

    def __post_init__(self) -> None:
        if not isinstance(self.invariants, tuple):
            raise InvariantSpecError("InvariantSet.invariants must be a tuple")
        seen: set[str] = set()
        for inv in self.invariants:
            if not isinstance(inv, InvariantSpec):
                raise InvariantSpecError(
                    f"InvariantSet.invariants member is not an InvariantSpec: {inv!r}"
                )
            if inv.id in seen:
                raise InvariantSpecError(
                    f"duplicate invariant id {inv.id!r} in InvariantSet"
                )
            seen.add(inv.id)
        for version_name, value in (
            ("spec_version", self.spec_version),
            ("claim_surface_version", self.claim_surface_version),
        ):
            if not value or not value.strip():
                raise InvariantSpecError(
                    f"InvariantSet.{version_name} must be non-empty (Pro §12 versioning)"
                )

    def for_claim(self, claim_id: str) -> tuple[InvariantSpec, ...]:
        """The invariants citing ``claim_id`` (the gate's per-claim join)."""
        return tuple(inv for inv in self.invariants if inv.claim_id == claim_id)

    @property
    def forbidden(self) -> tuple[InvariantSpec, ...]:
        """The invariants in the FORBIDDEN ``implicit_convention`` bucket (must be empty)."""
        return tuple(inv for inv in self.invariants if inv.is_forbidden)

    @property
    def invariant_set_root(self) -> str:
        return map_root(
            _DOMAIN_INVARIANT_SET_ROOT,
            {inv.id: inv.invariant_body_hash for inv in self.invariants},
        )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_INVARIANT_SET,
            "spec_version": self.spec_version,
            "claim_surface_version": self.claim_surface_version,
            "invariant_set_root": self.invariant_set_root,
            "invariants": [inv.to_canonical_dict() for inv in self.invariants],
        }

    def __len__(self) -> int:
        return len(self.invariants)


# --------------------------------------------------------------------------- #
# The v0 invariant rows — one live accounting path per v0 claim.               #
#                                                                              #
# Each row BINDS to a real production accounting path (a finding code / checker #
# / writer refusal) or an explicit declared non-guarantee, and cross-references #
# the prose audit-registry row via ``audit_registry_ref`` (NOT a fork).         #
# --------------------------------------------------------------------------- #

# --- Claim: bench agreement score ---------------------------------------- #
# The bench score is a projection (an EvidenceSurfaceReport with replay_claims=
# False); the live path is the report's own typed truth_claim + forbidden-
# shortcut declaration, which refuses to present the score as source truth /
# replay authority. The bounding non-guarantees are declared AssumptionRegister
# entries (declared_non_guarantee bucket).
_INV_BENCH_PROJECTION_FIREWALL = InvariantSpec(
    id="CSM-BENCH-01",
    claim_id="lawvm.fi.bench.agreement_score.v1",
    plane="projection",
    waist="evidence_surface",
    unit_kind="aggregate",
    predicate=(
        "the bench evidence surface declares replay_claims=False and a typed "
        "forbidden-shortcut set (bench_score_as_source_truth / "
        "bench_score_as_replay_authorization), so the score cannot be read as "
        "source truth or replay authorization"
    ),
    owner="lawvm.finland.bench_bundle_proof_projector",
    bucket="projection_verifier",
    checker_ref="lawvm.finland.bench_bundle_proof_projector:finland_bench_run_evidence_surface",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="(bench evidence-surface firewall; see PROJ plane)",
)
_INV_BENCH_NOT_SOURCE_TRUTH = InvariantSpec(
    id="CSM-BENCH-02",
    claim_id="lawvm.fi.bench.agreement_score.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the bench score is explicitly NOT source truth and NOT replay "
        "authorization — declared non-guarantees, not unstated convention"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_BENCH_AGREEMENT",
    finding_code="",
    root_membership="assumption_register_root",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: materialization / selection (UniverseSpec Wave-1) ------------ #
_INV_MATERIALIZATION_NO_SILENT_DROP = InvariantSpec(
    id="LS-MAT-01",
    claim_id="lawvm.fi.provision_state.selected.v1",
    plane="legal_state",
    waist="materialization",
    unit_kind="per-unit",
    predicate=(
        "every expected section unit in the root-committed UniverseSpec is "
        "PRESENT, BENIGN_ABSENT, TYPED_RESIDUAL, or a named SILENTLY_DROPPED_UNIT "
        "violation — per-unit materialization totality (the 1929/234 class), "
        "strictly stronger than aggregate-sum totality"
    ),
    owner="lawvm.finland.materialization_totality",
    bucket="implemented_check",
    checker_ref="lawvm.finland.materialization_totality:check_materialization_totality",
    finding_code="SILENTLY_DROPPED_UNIT",
    root_membership="universe_root",
    status="IMPL",
    audit_registry_ref="LS-04",
)
_INV_MATERIALIZATION_RELATIVE_TO_ABSENCES = InvariantSpec(
    id="LS-MAT-02",
    claim_id="lawvm.fi.provision_state.selected.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "a CLEAN materialization verdict is relative to the completeness of the "
        "caller-supplied typed-absence set, and the universe ranges over SECTION "
        "units only in v0 — declared boundaries, not unstated convention"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.finland.materialization_totality.UniverseSpec",
    finding_code="",
    root_membership="assumption_register_root",
    status="IMPL",
    audit_registry_ref="LS-04",
)

# --- Claim: reference classification (SURF-05) --------------------------- #
_INV_REFERENCE_CLASSIFICATION = InvariantSpec(
    id="SURF-05",
    claim_id="lawvm.fi.reference.classification.v1",
    plane="surface",
    waist="citation_totality",
    unit_kind="per-unit",
    predicate=(
        "every emitted ReferenceMention carries a cite_confidence in the closed "
        "CLASSIFIED set {resolved, statute_only, ambiguous, open, broken, "
        "unsupported}; an out-of-set value is a typed REFERENCE.UNCLASSIFIED_"
        "REFERENCE finding, never a silently widened set"
    ),
    owner="lawvm.finland.references.surface_totality",
    bucket="implemented_check",
    checker_ref="lawvm.finland.references.surface_totality:sweep_citation_totality",
    finding_code="REFERENCE.UNCLASSIFIED_REFERENCE",
    root_membership="",
    status="IMPL",
    audit_registry_ref="SURF-05",
)
_INV_REFERENCE_SURFACE_NOT_AUTHORITY = InvariantSpec(
    id="SURF-05-NG",
    claim_id="lawvm.fi.reference.classification.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "a reference classification is a SURFACE fact and never replay authority; "
        "resolution recall is bounded and an OPEN/UNSUPPORTED mention is an honest "
        "non-resolution, not a defect — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_REFERENCE_CLASSIFICATION",
    finding_code="",
    root_membership="assumption_register_root",
    status="IMPL",
    audit_registry_ref="SURF-05",
)

# --- Claim: source monotonicity (KNOW Wave-1) ---------------------------- #
_INV_KNOW_MONOTONICITY = InvariantSpec(
    id="KNOW-01",
    claim_id="lawvm.know.source_monotonicity.v1",
    plane="source",
    waist="source_record",
    unit_kind="per-unit",
    predicate=(
        "no AVAILABLE source locator carries two distinct content digests "
        "(KNOW-01 append-only); every UNCHECKABLE (digest-less) observation is "
        "reported separately, never counted a violation (KNOW-03)"
    ),
    owner="lawvm.core.know_invariants",
    bucket="implemented_check",
    checker_ref="lawvm.core.know_invariants:check_source_monotonicity",
    finding_code="EVID.SOURCE_LOCATOR_DIGEST_CONFLICT",
    root_membership="",
    status="IMPL",
    audit_registry_ref="SRC-06",
)
_INV_KNOW_DEFERRED = InvariantSpec(
    id="KNOW-02-04",
    claim_id="lawvm.know.source_monotonicity.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "KNOW-02 (latest answer NAMES its source policy) and KNOW-04 (retraction "
        "taint by graph query) are NOT yet checked — no populated subject/graph; "
        "deferred with owner core/know_invariants, not assumed to hold"
    ),
    owner="lawvm.core.know_invariants",
    bucket="deferred_with_owner",
    checker_ref="lawvm.core.know_invariants",
    finding_code="",
    root_membership="",
    status="PART",
    audit_registry_ref="",
)

# --- Claim: EU directive transposition + timeliness (Wave-2 #73) --------- #
_INV_EU_TRANSPOSITION_TIMELINESS = InvariantSpec(
    id="REF-EU-01",
    claim_id="lawvm.fi.eu.transposition_edge.v1",
    plane="evidence",
    waist="reference",
    unit_kind="per-unit",
    predicate=(
        "each transposition edge binds the directive CELEX from the nickname "
        "registry (or carries the unbound surface) and a timeliness verdict that is "
        "a pure date comparison computed ONLY when both enactment + deadline are "
        "known, else a typed UNKNOWN_* — never a fabricated date"
    ),
    owner="lawvm.finland.references.eu_transposition_edges",
    bucket="implemented_check",
    checker_ref="lawvm.finland.references.eu_transposition_edges:build_transposition_edges",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
_INV_EU_TRANSPOSITION_NOT_CONFORMANCE = InvariantSpec(
    id="REF-EU-02",
    claim_id="lawvm.fi.eu.transposition_edge.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the edge asserts the DECLARED transposition relation + timing only; it does "
        "NOT assess substantive conformance, the deadline seed is not exhaustive, and "
        "the FI enactment date is caller-supplied — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.finland.references.eu_transposition_edges:TranspositionEdge",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: transclusion typed-derivation edges (Wave-2 #74) ------------- #
_INV_DERIVATION_KINDS_DISTINCT = InvariantSpec(
    id="REF-DER-01",
    claim_id="lawvm.fi.derivation_edge.v1",
    plane="evidence",
    waist="reference",
    unit_kind="per-unit",
    predicate=(
        "each derivation edge is typed into exactly one of {textual, model_code, "
        "conformance, citation} and asserted matrix-legal (edge_authority_violation) "
        "before emission; a textual legal-state grant is carried by an explicit "
        "ExecutionAuthorization — forging a resemblance onto legal_state raises"
    ),
    owner="lawvm.finland.references.derivation_edges",
    bucket="implemented_check",
    checker_ref="lawvm.finland.references.derivation_edges:build_textual_edge",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
_INV_DERIVATION_CLOSED_KIND_SET = InvariantSpec(
    id="REF-DER-03",
    claim_id="lawvm.fi.derivation_edge.v1",
    plane="evidence",
    waist="reference",
    unit_kind="per-unit",
    predicate=(
        "the derivation-kind set {textual, model_code, conformance, citation} is "
        "CLOSED by construction (DerivationKind is an enum); an emitted edge that "
        "matches no kind is REFUSED by DerivationEdgeSet.kind_of (raises), never "
        "silently bucketed — the closed set cannot be widened at runtime"
    ),
    owner="lawvm.finland.references.derivation_edges",
    bucket="writer_refusal",
    checker_ref="lawvm.finland.references.derivation_edges:DerivationEdgeSet.kind_of",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
_INV_DERIVATION_DEDUP_NOT_AUTHORITY = InvariantSpec(
    id="REF-DER-02",
    claim_id="lawvm.fi.derivation_edge.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "textual derivation (shared bytes) does_not_imply lineage/conformance/"
        "citation; model-code lineage is typed-UNKNOWN (bytes_only_not_lineage), "
        "never guessed; conformance is claimed, not assessed — dedup is not authority"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.finland.references.derivation_edges:DerivationKind",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: counterfactual 3-tier bill effects (Wave-2 #72) -------------- #
_INV_COUNTERFACTUAL_TIERS = InvariantSpec(
    id="BRANCH-CF-01",
    claim_id="lawvm.fi.bill.counterfactual_effects.v1",
    plane="projection",
    waist="bill_effects",
    unit_kind="per-unit",
    predicate=(
        "every reported effect is partitioned into exactly one tier — direct "
        "(tier 1), resolved DEPENDENCY (tier 2, kept as THREE structurally distinct "
        "sub-tiers: provisions that CITE a changed section 1-hop, provisions reached "
        "through a bounded MULTI-HOP citation cascade at depth >= 2 each carrying "
        "its full hop chain, and provisions that USE a term DEFINED in a changed "
        "section), or the declared-uncomputed boundary (tier 3) — never conflated; "
        "the 1-hop and cascade arms never double-count (cascade reports depth >= 2 "
        "only); each tier-1/2 item carries provenance; no score/magnitude is emitted "
        "(BRANCH-06)"
    ),
    owner="lawvm.tools.bill_counterfactual_effects",
    bucket="implemented_check",
    checker_ref="lawvm.tools.bill_counterfactual_effects:build_counterfactual_report",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
_INV_COUNTERFACTUAL_UNCOMPUTED = InvariantSpec(
    id="BRANCH-CF-02",
    claim_id="lawvm.fi.bill.counterfactual_effects.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "tier-2 definition-users are SINGLE-HOP within the amended act (transitive "
        "definition chains, cross-act imported definitions, and open/ambiguous uses "
        "are NOT covered), the multi-hop citation cascade is computed to a declared "
        "maximum depth (reachers BEYOND that depth are uncomputed), "
        "semantic/teleological/temporal/transposition cascades are uncomputed, "
        "bare-section matching precision is bounded, and untraceable cites are typed "
        "external_only — all DECLARED in tier 3, not silently omitted"
    ),
    owner="lawvm.tools.bill_counterfactual_effects",
    bucket="declared_residual",
    checker_ref="lawvm.tools.bill_counterfactual_effects:build_tier_3_boundary",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: cross-jurisdiction materialization generality (Wave-2 #75) --- #
_INV_XJUR_GENERALITY = InvariantSpec(
    id="LS-MAT-XJ-01",
    claim_id="lawvm.xjur.materialization_totality_generality.v1",
    plane="legal_state",
    waist="materialization",
    unit_kind="per-unit",
    predicate=(
        "the jurisdiction-neutral materialization-totality core runs unmodified on a "
        "real Estonian replay tree (act 119062012020: 39 sections TOTAL; drop one -> "
        "INCOMPLETE + named SILENTLY_DROPPED_UNIT) as well as the FI 1929/234 witness"
    ),
    owner="lawvm.core.materialization_universe",
    bucket="implemented_check",
    checker_ref="lawvm.core.materialization_universe:check_materialization_totality",
    finding_code="SILENTLY_DROPPED_UNIT",
    root_membership="universe_root",
    status="IMPL",
    audit_registry_ref="LS-04",
)
_INV_XJUR_BOUNDARY = InvariantSpec(
    id="LS-MAT-XJ-02",
    claim_id="lawvm.xjur.materialization_totality_generality.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "generality is of ONE invariant implementation across jurisdictions; it is "
        "NOT bug-class portability, NOT reconstruction parity, and ranges over "
        "section units only — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.materialization_universe:UniverseSpec",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="LS-04",
)

# --- Claim: dangling references (tools/dangling_references.py) ------------ #
# TOTALITY: every RESOLVED reference lands in exactly one of the closed three-way
# statuses, and the report's own guard refuses a partition that does not sum
# (present + dangling + existence_unknown == resolved_checked) — a row that
# escaped classification is a typed finding, never a silent drop.
_INV_DANGLING_TOTALITY = InvariantSpec(
    id="REF-DANGLING-01",
    claim_id="lawvm.fi.reference.dangling.v1",
    plane="projection",
    waist="reference",
    unit_kind="per-unit",
    predicate=(
        "every RESOLVED reference (cite_confidence exact/approximate) read from "
        "the fi_refs projection is classified into exactly one of the closed "
        "three-way statuses {PRESENT, DANGLING, EXISTENCE_UNKNOWN}; the report "
        "constructor REFUSES a partition where present+dangling+existence_unknown "
        "!= resolved_checked (a reference that escaped the three-way status), so "
        "no resolved reference is silently dropped"
    ),
    owner="lawvm.tools.dangling_references",
    bucket="implemented_check",
    checker_ref="lawvm.tools.dangling_references:build_dangling_report",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# CLOSURE: the three-way status set is CLOSED by construction. DanglingReferenceRow
# (and the report fold) RAISE DanglingReferenceError on a status outside
# {PRESENT, DANGLING, EXISTENCE_UNKNOWN} — the set cannot be widened at runtime.
_INV_DANGLING_CLOSURE = InvariantSpec(
    id="REF-DANGLING-02",
    claim_id="lawvm.fi.reference.dangling.v1",
    plane="projection",
    waist="reference",
    unit_kind="per-unit",
    predicate=(
        "the three-way status set {PRESENT, DANGLING, EXISTENCE_UNKNOWN} is CLOSED "
        "by construction (DANGLING_STATUSES); a DanglingReferenceRow constructed "
        "with — or a classification fold producing — a status outside the set is "
        "REFUSED (raises DanglingReferenceError), never silently bucketed"
    ),
    owner="lawvm.tools.dangling_references",
    bucket="writer_refusal",
    checker_ref="lawvm.tools.dangling_references:DanglingReferenceRow",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# NON_GUARANTEE_COVERAGE: the dangling claim's declared boundaries — the existence
# oracle is AS-OF-NOW (not as-of-citing), is bounded by corpus completeness
# (absent/unmaterialized act -> EXISTENCE_UNKNOWN, never dangling), and ranges
# over RESOLVED refs at SECTION granularity only.
_INV_DANGLING_BOUNDARY = InvariantSpec(
    id="REF-DANGLING-03",
    claim_id="lawvm.fi.reference.dangling.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the existence oracle is AS-OF-NOW (the target's CURRENT consolidated "
        "text-state), NOT as-of-citing — a since-repealed target reads DANGLING "
        "here though it may not be a defect as-of-writing; the oracle is bounded "
        "by corpus completeness (an absent / contentAbsent act is "
        "EXISTENCE_UNKNOWN, never dangling); and the check ranges over RESOLVED "
        "refs at SECTION granularity only — declared boundaries, not unstated "
        "convention"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_DANGLING_REFERENCE",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

#: The v0 invariant rows (one live accounting path + declared boundary per claim).
V0_INVARIANTS: tuple[InvariantSpec, ...] = (
    _INV_BENCH_PROJECTION_FIREWALL,
    _INV_BENCH_NOT_SOURCE_TRUTH,
    _INV_MATERIALIZATION_NO_SILENT_DROP,
    _INV_MATERIALIZATION_RELATIVE_TO_ABSENCES,
    _INV_REFERENCE_CLASSIFICATION,
    _INV_REFERENCE_SURFACE_NOT_AUTHORITY,
    _INV_KNOW_MONOTONICITY,
    _INV_KNOW_DEFERRED,
    _INV_EU_TRANSPOSITION_TIMELINESS,
    _INV_EU_TRANSPOSITION_NOT_CONFORMANCE,
    _INV_DERIVATION_KINDS_DISTINCT,
    _INV_DERIVATION_CLOSED_KIND_SET,
    _INV_DERIVATION_DEDUP_NOT_AUTHORITY,
    _INV_COUNTERFACTUAL_TIERS,
    _INV_COUNTERFACTUAL_UNCOMPUTED,
    _INV_XJUR_GENERALITY,
    _INV_XJUR_BOUNDARY,
    _INV_DANGLING_TOTALITY,
    _INV_DANGLING_CLOSURE,
    _INV_DANGLING_BOUNDARY,
)


def v0_invariant_set() -> InvariantSet:
    """The v0 :class:`InvariantSet` bound to the v0 claim surface."""
    return InvariantSet(
        V0_INVARIANTS,
        spec_version=INVARIANT_SPEC_VERSION,
        claim_surface_version="v0",
    )


__all__ = [
    "ALLOWED_BUCKETS",
    "ALL_BUCKETS",
    "Bucket",
    "FORBIDDEN_BUCKET",
    "INVARIANT_SPEC_VERSION",
    "InvariantSet",
    "InvariantSpec",
    "InvariantSpecError",
    "InvariantStatus",
    "Plane",
    "V0_INVARIANTS",
    "v0_invariant_set",
]
