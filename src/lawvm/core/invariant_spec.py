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

# --- Claim: fixed-term / temporary whole-law expiry safety --------------- #
# TOTALITY: each version of the entry-into-force provision carrying a recognised
# whole-law fixed-term clause is resolved to a typed StatuteValidityBound, or, on
# an unparseable / conflicting / anaphoric-ambiguous clause, emits a strict_fail
# diagnostic — never a silent live "still in force" answer.
_INV_FIXED_TERM_RESOLVED_OR_STRICT = InvariantSpec(
    id="TEMPORAL-FIXED-TERM-01",
    claim_id="lawvm.fi.expiry.fixed_term.v1",
    plane="legal_state",
    waist="fixed_term_expiry",
    unit_kind="per-unit",
    predicate=(
        "each version of the entry-into-force provision carrying a recognised "
        "whole-law fixed-term/temporary expiry clause is either resolved to a "
        "typed StatuteValidityBound or emits a strict_fail diagnostic "
        "(TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE / _AMBIGUOUS / "
        "_ANAPHORA_AMBIGUOUS); a recognised clause is never silently degraded "
        "into a live 'still in force' answer"
    ),
    owner="lawvm.finland.fixed_term_expiry",
    bucket="implemented_check",
    checker_ref="lawvm.finland.fixed_term_expiry:extract_fixed_term_bounds",
    finding_code="TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# CLOSURE/REFUSAL: the bound carrier itself refuses a malformed bound — the
# inclusive/exclusive off-by-one discipline (expires_on > valid_until, i.e.
# expires_on = valid_until + 1 day via expires_on_from_valid_until) and the
# bound-kind/epistemic/provenance well-formedness are enforced by
# StatuteValidityBound.__post_init__ (raises ValueError), so a bound that would
# answer expiry wrongly cannot be constructed.
_INV_FIXED_TERM_BOUND_REFUSAL = InvariantSpec(
    id="TEMPORAL-FIXED-TERM-02",
    claim_id="lawvm.fi.expiry.fixed_term.v1",
    plane="legal_state",
    waist="fixed_term_expiry",
    unit_kind="per-unit",
    predicate=(
        "a StatuteValidityBound with expires_on <= valid_until (a broken "
        "inclusive/exclusive cutoff), a non-whole_statute scope, an upper_cap "
        "without earlier_termination_possible, or a duration-computed bound "
        "missing its arithmetic provenance / masquerading as a grammar_fact is "
        "REFUSED by StatuteValidityBound.__post_init__ (raises ValueError) — a "
        "bound that would answer expiry wrongly cannot be constructed"
    ),
    owner="lawvm.core.statute_validity",
    bucket="writer_refusal",
    checker_ref="lawvm.core.statute_validity:StatuteValidityBound",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# NON_GUARANTEE_COVERAGE: the declared boundaries of the fixed-term claim.
_INV_FIXED_TERM_BOUNDARY = InvariantSpec(
    id="TEMPORAL-FIXED-TERM-03",
    claim_id="lawvm.fi.expiry.fixed_term.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the inclusive valid_until vs exclusive expires_on convention is a "
        "STORED fact (expired at D iff D >= expires_on iff D > valid_until); an "
        "ambiguous/anaphoric expiry is a strict finding, never a guessed bound; "
        "and the VÄLIAIKAINEN temporary-amendment unresolved-expiry case is a "
        "WARN (TIME.UNRESOLVED_TEMPORARY_EXPIRY), a weaker separately-declared "
        "enforcement than the whole-law strict block — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_FIXED_TERM_EXPIRY",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: version-timeline integrity ----------------------------------- #
# TOTALITY: every provision timeline is swept for the three robust structural
# integrity properties (no ambiguous overlapping permanent versions, well-formed
# temporary overlay intervals, monotone expiry chains); each breach is a typed
# TimelineInvariantViolation carrying section attribution, never silent.
_INV_TIMELINE_INTEGRITY_TYPED = InvariantSpec(
    id="TIMELINE-INTEGRITY-01",
    claim_id="lawvm.fi.timeline.integrity.v1",
    plane="legal_state",
    waist="timeline",
    unit_kind="per-unit",
    predicate=(
        "every provision timeline is swept for the three robust structural "
        "integrity properties — overlapping/ambiguous permanent versions, "
        "temporary-overlay well-formedness (expiry present, non-inverted, "
        "non-overlapping), and expiry-chain monotonicity — and each breach is "
        "emitted as a typed TimelineInvariantViolation with its kind + section "
        "attribution; a breach is never silently tolerated"
    ),
    owner="lawvm.core.timeline_invariants",
    bucket="implemented_check",
    checker_ref="lawvm.core.timeline_invariants:check_all_timeline_invariants_typed",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# NON_GUARANTEE_COVERAGE: the declared boundaries of the timeline-integrity claim.
_INV_TIMELINE_INTEGRITY_BOUNDARY = InvariantSpec(
    id="TIMELINE-INTEGRITY-02",
    claim_id="lawvm.fi.timeline.integrity.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the claim covers the three ROBUST structural families "
        "(temporal_overlap / temporary_overlay / expiry_chain) only; the heavier "
        "replay/materialization-drift check (IR-vs-timeline consistency) is a "
        "SEPARATE materialization-variant-tier check, not one of the three "
        "structural properties; and a TimelineInvariantViolation is a DETECTION, "
        "never a legal conclusion or an automatic repair — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_TIMELINE_INTEGRITY",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: corpus Legal Surface Graph ----------------------------------- #
# TOTALITY: the corpus merge accounts EVERY per-statute node — each statute
# graph's nodes are merged into the union, and a same-node-id collision with a
# divergent payload_hash RAISES (SurfaceAssemblyError) rather than silently
# overwriting. So no per-statute node is silently dropped or silently mutated in
# the merge: a node either dedups idempotently (same payload_hash) or fails loud.
_INV_LSG_MERGE_TOTALITY = InvariantSpec(
    id="LSG-CORPUS-01",
    claim_id="lawvm.fi.legal_surface_graph.v1",
    plane="surface",
    waist="corpus_merge",
    unit_kind="per-unit",
    predicate=(
        "every per-statute surface node is merged into the corpus union; a "
        "same-node-id collision with a divergent payload_hash RAISES "
        "(SurfaceAssemblyError) rather than silently overwriting, and an "
        "identical-hash duplicate dedups idempotently to the one shared entity "
        "node — so no per-statute node is silently dropped or silently mutated "
        "across the cross-statute merge"
    ),
    owner="lawvm.finland.legal_surface.corpus_graph",
    bucket="implemented_check",
    checker_ref="lawvm.finland.legal_surface.corpus_graph:build_corpus_surface_graph",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# AUTHORITY_FIREWALL: every cross-statute edge is minted through the assembler's
# firewall-enforcing path (run_edge_passes -> _mint_edge_from_seed ->
# _enforce_edge_firewall), which RAISES AuthorityFirewallError on any node/edge
# carrying replay_authorized=True or surface_only=False. The corpus graph can
# never carry legal authority or replay authorization — it is structurally
# surface-only.
_INV_LSG_AUTHORITY_FIREWALL = InvariantSpec(
    id="LSG-CORPUS-02",
    claim_id="lawvm.fi.legal_surface_graph.v1",
    plane="firewall",
    waist="corpus_edge_pass",
    unit_kind="per-op",
    predicate=(
        "every cross-statute reference/candidate edge is minted through the "
        "assembler's firewall-enforcing path (run_edge_passes -> "
        "_enforce_edge_firewall), which RAISES AuthorityFirewallError on any "
        "node/edge with replay_authorized=True or surface_only=False; the corpus "
        "graph never carries legal authority and is never replay authorization — "
        "the firewall is structural, not a tunable threshold"
    ),
    owner="lawvm.core.legal_surface_assembler",
    bucket="writer_refusal",
    checker_ref="lawvm.core.legal_surface_assembler:run_edge_passes",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# NON_GUARANTEE_COVERAGE: the declared boundaries of the corpus-graph claim — the
# graph merges the reference + anaphora backbone AND three typed v2 families
# (definition-use, EU transposition, dangling status); the DERIVATION family alone
# is the declared remaining extension, resolution recall is bounded, and the graph
# is an as-of surface projection over the declared slice.
_INV_LSG_BOUNDARY = InvariantSpec(
    id="LSG-CORPUS-03",
    claim_id="lawvm.fi.legal_surface_graph.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "the graph merges the reference + anaphora backbone (refers_to / "
        "has_candidate) AND three typed relation families — definition-use "
        "(defines_term / uses_term), EU transposition (transposes), and "
        "dangling-reference status (the three-way existence verdict on each "
        "provision-target node); the DERIVATION edge family alone is NOT yet merged "
        "and is the DECLARED remaining extension (it needs a provision-pair "
        "candidate source the reference-only build does not produce); resolution "
        "recall is bounded (an open / statute_only mention is an honest "
        "non-promotion); and the graph is an as-of surface projection over the "
        "declared slice — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref="lawvm.core.claim_surface_manifest:CLAIM_LEGAL_SURFACE_GRAPH",
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# TRANSPOSITION_NOT_CONFORMANCE: the new ``transposes`` edge family carries a
# surface-only authority risk — a transposes edge could be MISREAD as a verified
# conformance / lineage assertion. The boundary is encoded structurally: the edge
# is minted through the assembler's firewall-enforcing path (surface_only), and the
# edge payload carries the explicit ``means=act_declares_it_transposes_directive``
# / ``does_not_imply=verified_conformance`` honesty markers from the corpus
# transposition pass. The edge is the DECLARED transposition relation (the act SAYS
# it transposes), NEVER a conformance conclusion (the substantive conformance
# assessment is outside the oracle — the same boundary the EU-transposition claim
# and the derivation conformance-absence edge already declare).
_INV_LSG_TRANSPOSITION_NOT_CONFORMANCE = InvariantSpec(
    id="LSG-CORPUS-04",
    claim_id="lawvm.fi.legal_surface_graph.v1",
    plane="firewall",
    waist="corpus_edge_pass",
    unit_kind="per-op",
    predicate=(
        "every corpus ``transposes`` edge is minted through the assembler's "
        "firewall-enforcing path (surface_only) and carries the explicit "
        "does_not_imply=verified_conformance honesty marker; it is the DECLARED "
        "transposition relation (the act SAYS it transposes the directive), NEVER "
        "a conformance conclusion — a CELEX-bound directive is an asserted "
        "declaration and an unbound directive is a candidate (tag-don't-guess), "
        "never an invented or verified-conformance edge"
    ),
    owner="lawvm.finland.legal_surface.corpus_graph",
    bucket="writer_refusal",
    checker_ref=(
        "lawvm.finland.legal_surface.corpus_graph:CorpusTranspositionEdgePass"
    ),
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)

# --- Claim: EE consolidation-error candidate (the adoption wedge) -------- #
# TOTALITY: the candidate builder is the SOLE producer and routes EVERY replay
# divergence to exactly one disposition — strong (adjudicated consolidation-side
# residual), triage (no residual record), or deliberately EXCLUDED (an
# adjudicated but non-consolidation-side residual, e.g. replay_bug /
# source_pathology) — so no divergence is silently dropped INTO a surfaced tier
# and none is silently asserted as a consolidation error. (build_..._candidates
# walks the full divergence stream; the exclusion arm is tested.)
_INV_EE_CONSERR_TOTALITY = InvariantSpec(
    id="EE-CONSERR-01",
    claim_id="lawvm.ee.consolidation_error_candidate.v1",
    plane="projection",
    waist="consolidation_error_candidate",
    unit_kind="per-unit",
    predicate=(
        "the candidate builder is the SOLE producer and routes EVERY replay "
        "divergence to exactly one disposition — STRONG (an adjudicated "
        "consolidation-side residual bucket, source_oracle_drift / "
        "oracle_correction_notice), TRIAGE (no residual record, flagged "
        "unadjudicated_needs_review), or deliberately EXCLUDED (an adjudicated "
        "non-consolidation-side residual, e.g. replay_bug / source_pathology) — "
        "so no divergence is silently dropped into a surfaced tier and none is "
        "silently asserted a consolidation error"
    ),
    owner="lawvm.estonia.consolidation_error_candidates",
    bucket="implemented_check",
    checker_ref=(
        "lawvm.estonia.consolidation_error_candidates:consolidation_error_candidates"
    ),
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# CLOSURE: the tier set {strong, triage} is CLOSED by construction. The builder
# only ever constructs a candidate with tier="strong" or tier="triage" (the two
# literals it passes to _make_candidate) and partitions them into two
# structurally-distinct report fields (strong_candidates / triage_candidates);
# there is no third tier and no path that widens the set at runtime — the
# builder is the verifying step that places each surfaced candidate into exactly
# one closed-tier field, ranked strong-first.
_INV_EE_CONSERR_CLOSURE = InvariantSpec(
    id="EE-CONSERR-02",
    claim_id="lawvm.ee.consolidation_error_candidate.v1",
    plane="projection",
    waist="consolidation_error_candidate",
    unit_kind="per-unit",
    predicate=(
        "the tier set {strong, triage} is CLOSED by construction: the builder "
        "constructs a candidate only with tier='strong' (when the adjudicated "
        "residual bucket is consolidation-side) or tier='triage' (when there is "
        "no residual record), and partitions them into the two "
        "structurally-distinct ConsolidationErrorCandidateReport fields "
        "(strong_candidates / triage_candidates) ranked strong-first; there is no "
        "third tier and no runtime path that widens the set"
    ),
    owner="lawvm.estonia.consolidation_error_candidates",
    bucket="checker_step",
    checker_ref=(
        "lawvm.estonia.consolidation_error_candidates:consolidation_error_candidates"
    ),
    finding_code="",
    root_membership="",
    status="IMPL",
    audit_registry_ref="",
)
# NON_GUARANTEE_COVERAGE: the declared boundaries of the candidate claim — a
# candidate is a FLAG for human review, never a confirmed error or legal
# conclusion; the surface DEPENDS on the upstream EE replay + residual
# adjudication it consumes (it never re-adjudicates); recall is bounded by that
# adjudication (an unadjudicated divergence is triage, never asserted strong);
# and it ranges over EE (base, oracle) act pairs only.
_INV_EE_CONSERR_BOUNDARY = InvariantSpec(
    id="EE-CONSERR-03",
    claim_id="lawvm.ee.consolidation_error_candidate.v1",
    plane="declaration",
    waist="claim_boundary",
    unit_kind="static",
    predicate=(
        "a consolidation-error candidate is a FLAG for human review, never a "
        "confirmed error or legal conclusion; the surface DEPENDS on the upstream "
        "EE replay divergence stream + residual adjudication it consumes and "
        "never re-adjudicates; recall is bounded by that adjudication (an "
        "unadjudicated divergence is TRIAGE, never asserted STRONG); and it "
        "ranges over EE (base, oracle) act pairs only — declared boundaries"
    ),
    owner="lawvm.core.assumption_register",
    bucket="declared_non_guarantee",
    checker_ref=(
        "lawvm.core.claim_surface_manifest:CLAIM_EE_CONSOLIDATION_ERROR_CANDIDATE"
    ),
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
    _INV_FIXED_TERM_RESOLVED_OR_STRICT,
    _INV_FIXED_TERM_BOUND_REFUSAL,
    _INV_FIXED_TERM_BOUNDARY,
    _INV_TIMELINE_INTEGRITY_TYPED,
    _INV_TIMELINE_INTEGRITY_BOUNDARY,
    _INV_LSG_MERGE_TOTALITY,
    _INV_LSG_AUTHORITY_FIREWALL,
    _INV_LSG_BOUNDARY,
    _INV_LSG_TRANSPOSITION_NOT_CONFORMANCE,
    _INV_EE_CONSERR_TOTALITY,
    _INV_EE_CONSERR_CLOSURE,
    _INV_EE_CONSERR_BOUNDARY,
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
