"""``lawvm.claim_assumption_binding.v0`` — handle → registered non-guarantee.

WHAT THIS CLOSES — the deferred per-handle boundary. A
:class:`~lawvm.core.claim_surface_manifest.ClaimSpec` declares its boundaries as
a tuple of stable STRING HANDLES in ``allowed_non_guarantees`` (e.g.
``"bench_score_not_source_truth"``). A
:class:`~lawvm.core.assumption_register.AssumptionRegister` is a CONTENT-ADDRESSED
non-guarantee record whose only key is its hash ``assumption_id`` — it carries NO
stable string handle. Until now those two planes did not meet: a claim handle
resolved to NOTHING, and the invariant generator's ``NON_GUARANTEE_COVERAGE``
question checked only that ≥1 boundary invariant existed per claim, explicitly
DEFERRING per-handle resolution. This module is the missing binding: an external,
declaration-plane map from each declared handle to one registered, well-formed
``AssumptionRegister`` entry, plus a resolver and a root over the binding set so
the binding set is itself committed and checkable.

WHY A SEPARATE PLANE (the load-bearing design constraint). The ``assumption_id``
is a content hash over the register entry's body and is the ONLY hash it carries;
adding a handle field to the hashed object would move every ``assumption_id`` and
the ``assumption_register_root`` — a silent migration. So the handle lives HERE,
in the declaration plane (the same plane as ``ClaimSurfaceManifest`` and the
``AssumptionRegister`` itself), NOT inside the hashed body. A
:class:`ClaimAssumptionBinding` pairs an external handle string with a register
entry; the entry's hashed schema is untouched. The binding set's own root is a
``MapRoot`` over ``{handle: assumption_id}`` — adding, dropping, or editing any
binding changes the root, exactly as ``ClaimSurfaceManifest.manifest_root`` and
``assumption_register_root`` make their sets checkable.

THE HONESTY BOUNDARY (constructive-invariant pattern — declare what this proves
and what it does NOT). A binding asserts that the handle is a DECLARED,
registered non-guarantee — it names a real :class:`AssumptionRegister` with a
real ``kind`` / ``effect`` / ``expires_when`` / ``public_message`` specific to
that boundary. It does **NOT** assert that the boundary is complete, that the gap
is harmless, or that the registered assumption is the FULL story of the claim's
non-guarantees. Resolution proves a declared assumption EXISTS for the handle, NOT
that it is true, minimal, or exhaustive (the ``AssumptionRegister`` module
docstring's garbage-in caveat still holds). Completeness is claim-relative and
VERSIONED (Pro §12): v0 covers exactly the handles declared by ``V0_CLAIMS``; a
NEW handle with no binding FAILS the per-handle coverage gate
(:mod:`tests.test_claim_assumption_binding`). Expanding the claim surface
regenerates the binding set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lawvm.core.assumption_register import (
    AssumptionEffect,
    AssumptionKind,
    AssumptionRegister,
)
from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import map_root

_SCHEMA_CLAIM_ASSUMPTION_BINDING = "lawvm.claim_assumption_binding.v0"
_DOMAIN_CLAIM_ASSUMPTION_BINDING_ROOT = "claim_assumption_binding"

#: The declared binding-set version. Bumping it widens/changes the covered
#: handle set (Pro §12: completeness is claim-relative and VERSIONED).
CLAIM_ASSUMPTION_BINDING_VERSION = "v0"


class ClaimAssumptionBindingError(ValueError):
    """A claim-assumption-binding object violates a v0 schema invariant."""


@dataclass(frozen=True, slots=True)
class ClaimAssumptionBinding:
    """``lawvm.claim_assumption_binding.v0`` — one handle → one registered assumption.

    A frozen declaration-plane record pairing a stable non-guarantee HANDLE (the
    string a :class:`~lawvm.core.claim_surface_manifest.ClaimSpec` carries in
    ``allowed_non_guarantees``) with the :class:`AssumptionRegister` entry that
    declares it. The handle is external to the hashed assumption body (see module
    docstring): binding here does NOT perturb the assumption's ``assumption_id``
    or the ``assumption_register_root``.

    Fields:

    * ``handle`` — the stable non-guarantee handle declared by some claim.
    * ``assumption`` — the registered ``AssumptionRegister`` entry for it (a real
      ``kind`` / ``effect`` / ``expires_when`` / ``public_message``).
    """

    handle: str
    assumption: AssumptionRegister

    def __post_init__(self) -> None:
        if not self.handle or not self.handle.strip():
            raise ClaimAssumptionBindingError(
                "ClaimAssumptionBinding.handle must be a non-empty stable handle"
            )
        if not isinstance(self.assumption, AssumptionRegister):
            raise ClaimAssumptionBindingError(
                f"ClaimAssumptionBinding {self.handle!r} assumption must be an "
                f"AssumptionRegister, got {type(self.assumption).__name__}"
            )

    @property
    def assumption_id(self) -> str:
        """The bound assumption's content id (its hash; unaffected by the handle)."""
        return self.assumption.assumption_id

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CLAIM_ASSUMPTION_BINDING,
            "handle": nfc(self.handle),
            "assumption_id": self.assumption_id,
        }


# --------------------------------------------------------------------------- #
# The v0 binding set — every V0_CLAIMS handle bound to a registered assumption.#
#                                                                              #
# Each entry below is a DECLARED, registered non-guarantee with a real         #
# kind/scope/effect/expires_when/public_message honest and specific to the     #
# boundary the handle names. None of these reuse the FI compile-group-scope    #
# entry in fi_assumptions (which declares a DIFFERENT, unrelated boundary —    #
# the B2 source-body-over-prior-repeal fork — and maps to no V0_CLAIMS         #
# handle); all 21 are new, claim-surface-specific entries.                     #
# --------------------------------------------------------------------------- #


def _binding(
    handle: str,
    *,
    kind: AssumptionKind,
    scope: str,
    effect: AssumptionEffect,
    expires_when: str,
    public_message: str,
    witness_rule_id: str | None = None,
    finding_refs: tuple[str, ...] = (),
) -> ClaimAssumptionBinding:
    return ClaimAssumptionBinding(
        handle=handle,
        assumption=AssumptionRegister(
            kind=kind,
            scope=scope,
            effect=effect,
            expires_when=expires_when,
            public_message=public_message,
            witness_rule_id=witness_rule_id,
            finding_refs=finding_refs,
        ),
    )


# --- Claim: bench agreement score ---------------------------------------- #
_B_BENCH_NOT_SOURCE_TRUTH = _binding(
    "bench_score_not_source_truth",
    kind="projection_unverified",
    scope=(
        "The Finland benchmark mean structural/Levenshtein agreement score over "
        "the corpus, as emitted by the bench evidence surface "
        "(lawvm.finland.bench_bundle_proof_projector)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — the score is a regression projection by construction; a "
        "source-truth claim would require an independent oracle of governing text, "
        "which the bench does not consume."
    ),
    public_message=(
        "The bench agreement score is regression evidence of how closely replayed "
        "provision text tracks the oracle. It is NOT a claim that the replayed "
        "text IS the source truth; a high score is not a guarantee of correctness "
        "of any individual provision."
    ),
)
_B_BENCH_NOT_REPLAY_AUTH = _binding(
    "bench_score_not_replay_authorization",
    kind="projection_unverified",
    scope=(
        "Using the Finland benchmark agreement score as authorization to execute "
        "or trust a replay (forbidden shortcut bench_score_as_replay_authorization)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — replay authorization is carried by ExecutionAuthorization "
        "on the legal-state plane, never by an evidence-surface aggregate score; "
        "the firewall is structural, not a tunable threshold."
    ),
    public_message=(
        "The bench agreement score does NOT authorize replay. No score threshold "
        "grants execution authority; replay authorization lives on the legal-state "
        "plane and is independent of any benchmark aggregate."
    ),
)

# --- Claim: materialization / selection ---------------------------------- #
_B_MAT_RELATIVE_ABSENCES = _binding(
    "materialization_clean_relative_to_supplied_typed_absences",
    kind="doctrine_unresolved",
    scope=(
        "Whether a CLEAN materialization-totality verdict is unconditional, vs "
        "relative to the completeness of the caller-supplied typed-absence set "
        "(lawvm.finland.materialization_totality)."
    ),
    effect="qualifies",
    expires_when=(
        "an independent oracle of legitimate-repeal / benign-absence reasons "
        "exists that does not depend on the caller supplying the typed-absence set "
        "(a real repeal with no surviving tombstone and no supplied reason is "
        "reported as a VIOLATION by design until then)."
    ),
    public_message=(
        "A CLEAN materialization verdict is clean RELATIVE to the typed-absence "
        "set the caller supplies. A legitimate repeal with no surviving tombstone "
        "and no supplied reason reports as a VIOLATION by design — completeness of "
        "the absence set is the caller's, not LawVM's, guarantee."
    ),
)
_B_MAT_SECTION_UNITS_ONLY = _binding(
    "materialization_universe_section_units_only",
    kind="parser_incomplete",
    scope=(
        "The unit kind the materialization-totality universe ranges over: SECTION "
        "(pykälä) units only in v0; sub-section, paragraph, and other unit kinds "
        "are out of the v0 universe."
    ),
    effect="qualifies",
    expires_when=(
        "the UniverseSpec is extended to enumerate sub-section / paragraph unit "
        "kinds and the per-unit totality check ranges over them."
    ),
    public_message=(
        "The per-unit materialization-totality guarantee ranges over SECTION units "
        "only in v0. A silent drop at a finer granularity (a sub-section, a "
        "paragraph) is NOT covered by the v0 universe."
    ),
)

# --- Claim: reference classification -------------------------------------- #
_B_REF_SURFACE_NOT_AUTHORITY = _binding(
    "reference_classification_is_surface_not_replay_authority",
    kind="doctrine_unresolved",
    scope=(
        "Whether a reference classification (resolved / statute_only / ...) carries "
        "any legal-state or replay authority over the cited provision "
        "(lawvm.finland.references.surface_totality)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — a reference is a SURFACE fact by construction; authorizing "
        "a mutation from a resolved reference would forge surface evidence onto the "
        "legal-state plane (the authority firewall forbids it)."
    ),
    public_message=(
        "A reference classification is a SURFACE fact, not replay authority. A "
        "RESOLVED reference does NOT authorize any mutation of the cited provision; "
        "it records that a citation was recognised and bound, nothing more."
    ),
)
_B_REF_RECALL_BOUNDED = _binding(
    "reference_resolution_recall_bounded_open_is_honest",
    kind="parser_incomplete",
    scope=(
        "Reference-resolution recall: an OPEN or UNSUPPORTED mention is an honest "
        "non-resolution, not a defect; recall is bounded by the resolver's coverage "
        "(lawvm.finland.references.surface_totality)."
    ),
    effect="qualifies",
    expires_when=(
        "resolver coverage is extended such that a mention currently classified "
        "OPEN/UNSUPPORTED becomes resolvable — measured against the citation "
        "totality sweep, not asserted complete."
    ),
    public_message=(
        "Reference resolution recall is BOUNDED. An OPEN or UNSUPPORTED mention is "
        "an honest declared non-resolution, not a bug — LawVM does not guarantee "
        "every emitted mention resolves to a target."
    ),
)

# --- Claim: source monotonicity (KNOW) ------------------------------------ #
_B_KNOW_02_NOT_CHECKED = _binding(
    "know_02_source_policy_not_checked_no_subject",
    kind="parser_incomplete",
    scope=(
        "KNOW-02 (the latest source answer NAMES its governing source policy): not "
        "checked in v0 — no populated subject exists (lawvm.core.know_invariants)."
    ),
    effect="blocks_clean",
    expires_when=(
        "a populated source-policy subject exists for KNOW-02 to range over and a "
        "check that the latest answer names its policy is wired into "
        "lawvm.core.know_invariants."
    ),
    public_message=(
        "KNOW-02 (latest source answer names its source policy) is NOT checked in "
        "v0 — there is no populated subject to range over yet. The boundary is "
        "declared and owned by core/know_invariants, not assumed to hold."
    ),
)
_B_KNOW_04_NOT_CHECKED = _binding(
    "know_04_retraction_taint_not_checked_no_graph",
    kind="parser_incomplete",
    scope=(
        "KNOW-04 (retraction taint propagation by graph query): not checked in v0 — "
        "no populated retraction graph exists (lawvm.core.know_invariants)."
    ),
    effect="blocks_clean",
    expires_when=(
        "a populated retraction graph exists and a taint-propagation query is "
        "wired into lawvm.core.know_invariants."
    ),
    public_message=(
        "KNOW-04 (retraction taint by graph query) is NOT checked in v0 — there is "
        "no populated retraction graph to query. The boundary is declared and "
        "owned by core/know_invariants, not assumed to hold."
    ),
)
_B_KNOW_AVAILABLE_RECORDED = _binding(
    "know_available_is_recorded_digest_not_liveness_probe",
    kind="source_unavailable",
    scope=(
        "An AVAILABLE source observation asserts a RECORDED content digest, not a "
        "live re-fetch of the external locator (lawvm.core.know_invariants)."
    ),
    effect="qualifies",
    expires_when=(
        "a live re-fetch / liveness-probe capability is added that re-resolves the "
        "external locator at check time, distinguishing recorded-digest from "
        "currently-live."
    ),
    public_message=(
        "An AVAILABLE source observation asserts a RECORDED digest, not a live "
        "re-fetch. LawVM does not guarantee the external locator currently serves "
        "those bytes — only that the recorded observation held that digest."
    ),
)

# --- Claim: EU directive transposition ------------------------------------ #
_B_TRANS_NOT_CONFORMANCE = _binding(
    "transposition_edge_not_substantive_conformance",
    kind="doctrine_unresolved",
    scope=(
        "Whether a typed transposition edge asserts substantive conformance of the "
        "Finnish act to the EU directive, vs only the DECLARED relation + timing "
        "(lawvm.finland.references.eu_transposition_edges)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — substantive conformance is a legal-doctrine judgement with "
        "no compile-time discriminator; the edge records the act's OWN declared "
        "transposition relation and a date comparison only."
    ),
    public_message=(
        "A transposition edge records that an act declares it transposes a named "
        "directive, plus a timeliness date comparison. It is NOT an assessment of "
        "substantive conformance — LawVM does not claim the act correctly "
        "implements the directive's content."
    ),
)
_B_TRANS_DEADLINE_SEED = _binding(
    "transposition_deadline_seed_not_complete",
    kind="source_unavailable",
    scope=(
        "The directive-transposition-deadline seed registry is not exhaustive; a "
        "directive absent from the seed yields a typed UNKNOWN timeliness verdict "
        "(lawvm.finland.references.eu_transposition_edges)."
    ),
    effect="qualifies",
    expires_when=(
        "the transposition-deadline seed is extended to cover every directive a "
        "transposition edge can bind, or sourced from an authoritative deadline "
        "registry."
    ),
    public_message=(
        "The transposition-deadline seed is NOT complete. A directive missing from "
        "the seed produces a typed UNKNOWN timeliness verdict, never a guessed "
        "deadline — the boundary is declared, not silently filled."
    ),
)
_B_TRANS_FI_ENACTMENT_CALLER = _binding(
    "transposition_fi_enactment_date_caller_supplied",
    kind="source_unavailable",
    scope=(
        "The Finnish act's enactment date used in the timeliness comparison is "
        "caller-supplied, not independently derived "
        "(lawvm.finland.references.eu_transposition_edges)."
    ),
    effect="qualifies",
    expires_when=(
        "the FI enactment date is derived from the act's own structured metadata "
        "and no longer depends on a caller-supplied value."
    ),
    public_message=(
        "The Finnish enactment date in a transposition timeliness verdict is "
        "caller-supplied. LawVM does not independently verify it; the verdict is "
        "only as correct as the supplied date."
    ),
)

# --- Claim: derivation edges ---------------------------------------------- #
_B_DER_TEXTUAL_NOT_LINEAGE = _binding(
    "derivation_textual_is_bytes_not_lineage",
    kind="doctrine_unresolved",
    scope=(
        "Whether a textual-derivation edge (shared bytes between two texts) implies "
        "lineage, conformance, or citation (lawvm.finland.references.derivation_edges)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — shared bytes are decidable; lineage/conformance/citation "
        "are not implied by them and have no byte-level discriminator (the "
        "authority matrix forbids conflating the four kinds)."
    ),
    public_message=(
        "A textual-derivation edge records SHARED BYTES only. It does NOT imply "
        "that one text is derived from, conforms to, or cites the other — textual "
        "resemblance is not lineage."
    ),
)
_B_DER_MODEL_CODE_UNKNOWN = _binding(
    "derivation_model_code_lineage_not_byte_decidable",
    kind="doctrine_unresolved",
    scope=(
        "Model-code kinship (shared model-law ancestry) between two texts: typed "
        "UNKNOWN, never guessed — no byte-level discriminator decides it "
        "(lawvm.finland.references.derivation_edges)."
    ),
    effect="qualifies",
    expires_when=(
        "an external model-law lineage oracle exists that decides kinship; until "
        "then model-code lineage is typed UNKNOWN, never inferred from bytes."
    ),
    public_message=(
        "Model-code lineage between texts is typed UNKNOWN, never guessed. LawVM "
        "does not decide shared model-law ancestry from byte overlap — there is no "
        "byte-level discriminator for it."
    ),
)
_B_DER_CONFORMANCE_CLAIMED = _binding(
    "derivation_conformance_claimed_not_assessed",
    kind="doctrine_unresolved",
    scope=(
        "An EU-conformance derivation edge records a CLAIMED conformance relation, "
        "not an assessed one (lawvm.finland.references.derivation_edges)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — substantive conformance assessment is a legal-doctrine "
        "judgement with no compile-time discriminator; the edge records the "
        "declared relation only."
    ),
    public_message=(
        "A conformance derivation edge records a CLAIMED conformance relation, not "
        "an assessment. LawVM does not verify that the texts substantively conform."
    ),
)

# --- Claim: counterfactual bill effects ----------------------------------- #
_B_CF_DEFINITION_USERS_SINGLE_HOP = _binding(
    "counterfactual_definition_users_single_hop_only",
    kind="parser_incomplete",
    scope=(
        "Tier-2 definition-users (provisions that USE a term defined in a "
        "tier-1-changed section) are computed SINGLE-HOP, within the amended act "
        "only, from RESOLVED binding↔use edges of its definition graph "
        "(lawvm.tools.bill_counterfactual_effects)."
    ),
    effect="qualifies",
    expires_when=(
        "transitive definition CHAINS, CROSS-ACT imported definitions, and "
        "open/ambiguous uses are all resolved into the definition-user set; until "
        "then they are declared uncomputed in tier 3."
    ),
    public_message=(
        "Counterfactual definition-users are SINGLE-HOP and within the amended act "
        "only: a provision using a term whose definition was changed is included "
        "ONLY via a resolved binding↔use edge of the amended act's own definition "
        "graph. Transitive definition chains, cross-act imported definitions, and "
        "uses the graph leaves open/ambiguous are DECLARED uncomputed in tier 3, "
        "never silently treated as absent."
    ),
)
_B_CF_CASCADE_BOUNDED_SEMANTIC_UNCOMPUTED = _binding(
    "counterfactual_cascade_bounded_depth_semantic_uncomputed",
    kind="doctrine_unresolved",
    scope=(
        "The MULTI-HOP citation cascade IS computed to a declared maximum depth "
        "(bounded back-reference traversal over the amended act's resolved internal "
        "citations); reachers BEYOND that depth, and semantic/teleological/temporal/"
        "transposition effects, remain uncomputed and declared in tier 3 "
        "(lawvm.tools.bill_counterfactual_effects)."
    ),
    effect="outside_claim",
    expires_when=(
        "an oracle-grounded SEMANTIC/teleological cascade computation exists that "
        "does not manufacture false precision, and the cascade depth bound is "
        "lifted; until then beyond-depth cascades and semantic effects are declared "
        "uncomputed in tier 3."
    ),
    public_message=(
        "Counterfactual effects compute direct (tier 1) effects and, within tier 2, "
        "1-hop resolved citations PLUS a bounded MULTI-HOP citation cascade (a "
        "provision that cites a provision that … cites a changed section, to a "
        "declared maximum depth, each reacher carrying its full hop chain). Reachers "
        "BEYOND that bounded depth, and semantic / teleological / temporal / "
        "transposition effects, are DECLARED uncomputed in tier 3, never silently "
        "treated as absent."
    ),
)
_B_CF_BARE_SECTION_BOUNDED = _binding(
    "counterfactual_bare_section_precision_bounded",
    kind="parser_incomplete",
    scope=(
        "Bare-section reference matching precision is bounded; an untraceable cite "
        "is typed external_only (lawvm.tools.bill_counterfactual_effects)."
    ),
    effect="qualifies",
    expires_when=(
        "bare-section matching is disambiguated against the resolved reference "
        "graph such that external_only fallbacks are eliminated."
    ),
    public_message=(
        "Counterfactual bare-section matching precision is BOUNDED. A cite that "
        "cannot be traced to a section is typed external_only — the tier-2 set may "
        "miss or over-include a bare-section match, declared not silent."
    ),
)

# --- Claim: cross-jurisdiction generality --------------------------------- #
_B_XJUR_NOT_BUG_PORTABILITY = _binding(
    "xjur_not_bug_class_portability",
    kind="doctrine_unresolved",
    scope=(
        "Whether running the same materialization-totality core on Estonia + "
        "Finland implies the SAME bug classes are portable across jurisdictions "
        "(lawvm.core.materialization_universe)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — generality is of ONE invariant implementation, not of the "
        "jurisdiction-specific defect classes it could catch; portability of bug "
        "classes is a separate, unmade claim."
    ),
    public_message=(
        "Cross-jurisdiction generality is of ONE invariant implementation running "
        "unmodified, NOT a claim that the same bug classes are portable across "
        "jurisdictions."
    ),
)
_B_XJUR_NOT_RECON_PARITY = _binding(
    "xjur_not_reconstruction_parity",
    kind="doctrine_unresolved",
    scope=(
        "Whether the cross-jurisdiction materialization-totality evidence implies "
        "reconstruction parity (equal replay/reconstruction fidelity) across "
        "jurisdictions (lawvm.core.materialization_universe)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — reconstruction parity is a separate, measured claim about "
        "replay fidelity; the generality evidence is about the totality invariant "
        "running unmodified, not about equal reconstruction quality."
    ),
    public_message=(
        "Cross-jurisdiction generality does NOT imply reconstruction parity. The "
        "evidence shows one totality invariant runs unmodified across "
        "jurisdictions, not that replay fidelity is equal between them."
    ),
)
_B_XJUR_SECTION_UNITS_ONLY = _binding(
    "xjur_section_units_only",
    kind="parser_incomplete",
    scope=(
        "The cross-jurisdiction materialization-totality evidence ranges over "
        "SECTION units only (lawvm.core.materialization_universe)."
    ),
    effect="qualifies",
    expires_when=(
        "the cross-jurisdiction universe is extended to finer unit kinds "
        "(sub-section / paragraph) across the demonstrated jurisdictions."
    ),
    public_message=(
        "The cross-jurisdiction materialization-totality evidence ranges over "
        "SECTION units only. Generality at finer granularity is not demonstrated "
        "in v0."
    ),
)


# --- Claim: dangling references ------------------------------------------- #
_B_DANGLING_AS_OF_NOW = _binding(
    "dangling_existence_oracle_as_of_now_not_as_of_citing",
    kind="doctrine_unresolved",
    scope=(
        "The dangling-reference existence oracle checks the cited provision "
        "against the target act's CURRENT consolidated text-state (as-of-NOW), "
        "NOT as-of the citation's valid_at interval "
        "(lawvm.tools.dangling_references)."
    ),
    effect="qualifies",
    expires_when=(
        "the existence check is run as a point-in-time replay as-of the citation's "
        "valid_at start (the heavier broken-refs --provenance path), so a target "
        "that existed when cited but was repealed/renumbered since is "
        "distinguished from one that never existed."
    ),
    public_message=(
        "A DANGLING verdict means the cited provision is absent in the target "
        "act's CURRENT consolidated text-state (as-of-NOW). A reference whose "
        "target existed WHEN THE CITATION WAS WRITTEN but was repealed or "
        "renumbered since reads DANGLING here, yet may NOT be a defect "
        "as-of-writing — the as-of-citing check is the declared residual, not done "
        "in this projection."
    ),
)
_B_DANGLING_CORPUS_INCOMPLETE = _binding(
    "dangling_existence_oracle_current_state_incomplete_corpus",
    kind="source_unavailable",
    scope=(
        "The dangling-reference existence oracle reads the target act's current "
        "consolidated oracle XML from the local corpus; a target act absent from "
        "the corpus, or carrying a contentAbsent (unmaterialized) body, yields "
        "EXISTENCE_UNKNOWN (lawvm.tools.dangling_references)."
    ),
    effect="blocks_clean",
    expires_when=(
        "the local corpus materializes every target act a resolved reference can "
        "point at, so an EXISTENCE_UNKNOWN reflects a real non-determination "
        "rather than a corpus gap."
    ),
    public_message=(
        "The dangling check is bounded by corpus completeness. A target act that "
        "is absent from the corpus, or whose body is a contentAbsent placeholder "
        "(blocked / not-materialized), is reported EXISTENCE_UNKNOWN — NEVER "
        "DANGLING. A high EXISTENCE_UNKNOWN count is an honest non-determination, "
        "not a defect rate."
    ),
)
_B_DANGLING_RESOLVED_SECTION_SCOPE = _binding(
    "dangling_resolved_only_scope_section_granularity",
    kind="parser_incomplete",
    scope=(
        "The dangling check ranges over RESOLVED references only "
        "(cite_confidence exact/approximate) and resolves existence at SECTION "
        "(and embedded CHAPTER) granularity; momentti/kohta/alakohta below a "
        "present section are not separately checked "
        "(lawvm.tools.dangling_references)."
    ),
    effect="qualifies",
    expires_when=(
        "the existence oracle resolves below-section granularity (momentti / "
        "kohta / alakohta) and the in-scope confidence set is justified-extended."
    ),
    public_message=(
        "The dangling check covers RESOLVED references (exact/approximate) at "
        "SECTION granularity. A reference to a momentti/kohta within a section "
        "that IS present reads PRESENT — sub-section existence is not separately "
        "verified — and non-resolved references (statute_only/ambiguous/open/...) "
        "are out of scope and counted separately, not checked."
    ),
)


# --- Claim: fixed-term / temporary whole-law expiry safety ---------------- #
_B_FIXED_TERM_INCLUSIVE_EXCLUSIVE = _binding(
    "fixed_term_inclusive_valid_until_vs_exclusive_expires_on",
    kind="doctrine_unresolved",
    scope=(
        "The inclusive source valid_until (the law is in force ON that date) vs "
        "the kernel's exclusive expires_on cutoff (a version drops out once "
        "as_of >= expires_on); the convention is expires_on = valid_until + 1 "
        "day, so a statute is expired at D iff D >= expires_on iff D > "
        "valid_until (lawvm.core.statute_validity)."
    ),
    effect="qualifies",
    expires_when=(
        "never for v0 — the inclusive/exclusive split is a deliberate stored "
        "convention (both valid_until and expires_on are stored on the bound and "
        "validated expires_on > valid_until); it is not a defect to be removed "
        "but a boundary a consumer must read correctly."
    ),
    public_message=(
        "A fixed-term statute is expired at date D iff D is at or after "
        "expires_on, the EXCLUSIVE cutoff, which equals the INCLUSIVE source "
        "last-in-force day valid_until PLUS ONE DAY. A consumer comparing "
        "against valid_until directly (inclusive) versus expires_on (exclusive) "
        "must use the right one — the two differ by exactly one day by design."
    ),
)
_B_FIXED_TERM_AMBIGUOUS_STRICT = _binding(
    "fixed_term_ambiguous_expiry_is_strict_finding_not_guess",
    kind="doctrine_unresolved",
    scope=(
        "A whole-law fixed-term clause whose validity date cannot be parsed, "
        "whose two bounds conflict on the same effective date, or whose "
        "anaphoric year ('sanotun vuoden loppuun') has multiple plausible "
        "same-sentence antecedents (lawvm.finland.fixed_term_expiry)."
    ),
    effect="blocks_clean",
    expires_when=(
        "never for v0 — an unparseable/conflicting/genuinely-ambiguous expiry "
        "date is raised as a strict_fail finding rather than guessed; resolving "
        "it would require source disambiguation, not a default-fill heuristic."
    ),
    public_message=(
        "When a whole-law fixed-term expiry clause cannot be parsed, two bounds "
        "conflict on the same effective date, or the anaphoric year is "
        "genuinely ambiguous, LawVM raises a STRICT finding "
        "(TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE / _AMBIGUOUS / "
        "_ANAPHORA_AMBIGUOUS) — it never guesses the validity end and never "
        "emits a live 'still in force' answer over an unsafe bound."
    ),
)
_B_FIXED_TERM_TEMPORARY_WARN = _binding(
    "fixed_term_temporary_unresolved_expiry_is_warn_not_strict",
    kind="parser_incomplete",
    scope=(
        "The VÄLIAIKAINEN (temporary) amendment whose expiry date is "
        "unparseable: emitted as a temporary version WITHOUT expiry under a WARN "
        "finding (TIME.UNRESOLVED_TEMPORARY_EXPIRY), a weaker enforcement than "
        "the whole-law fixed-term strict block "
        "(lawvm.core.observation_registry)."
    ),
    effect="qualifies",
    expires_when=(
        "the temporary-amendment unresolved-expiry path is hardened to a strict "
        "block, or the expiry parser covers the currently-unparseable temporary "
        "forms so the WARN no longer fires."
    ),
    public_message=(
        "The VÄLIAIKAINEN temporary-amendment unresolved-expiry case is a WARN "
        "(TIME.UNRESOLVED_TEMPORARY_EXPIRY), NOT a strict block: such a version "
        "is emitted as temporary-without-expiry. This is a deliberately weaker "
        "enforcement than the whole-law fixed-term strict-fail discipline — a "
        "declared boundary, not silent."
    ),
)

# --- Claim: version-timeline integrity ------------------------------------ #
_B_TIMELINE_ROBUST_REPLAY_SEPARATE = _binding(
    "timeline_integrity_robust_families_replay_drift_separate",
    kind="parser_incomplete",
    scope=(
        "The version-timeline integrity claim covers the three ROBUST structural "
        "families (temporal_overlap / temporary_overlay / expiry_chain); the "
        "replay/materialization-drift check (IR-vs-timeline consistency, "
        "check_replay_timeline_consistency) is a SEPARATE "
        "materialization-variant-tier check (lawvm.core.timeline_invariants)."
    ),
    effect="qualifies",
    expires_when=(
        "the replay/materialization-drift consistency check is brought into the "
        "same robust-tier guarantee surface as the three structural families, "
        "rather than carried as a separate materialization-variant tier."
    ),
    public_message=(
        "The timeline-integrity guarantee covers the three ROBUST structural "
        "properties (no overlapping permanent versions, well-formed temporary "
        "overlays, monotone expiry chains). The heavier IR-vs-timeline "
        "replay-drift check is a SEPARATE, materialization-variant-tier check — "
        "it is not one of the three structural properties this claim asserts."
    ),
)
_B_TIMELINE_DETECTION_NOT_CONCLUSION = _binding(
    "timeline_integrity_violation_is_detection_not_conclusion",
    kind="doctrine_unresolved",
    scope=(
        "Whether a TimelineInvariantViolation is a legal conclusion or an "
        "automatic repair, vs a typed DETECTION only "
        "(lawvm.core.timeline_invariants)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — a timeline invariant violation records a detected "
        "structural inconsistency in the compiled timeline; resolving it (which "
        "version governs, or whether the source is itself defective) is a "
        "separate legal/editorial judgement the check does not make."
    ),
    public_message=(
        "A TimelineInvariantViolation is a DETECTION of a structural timeline "
        "inconsistency, not a legal conclusion and not an automatic repair. "
        "LawVM surfaces the breach with its kind and section attribution; it "
        "does NOT decide which version governs or edit the timeline to fix it."
    ),
)


# --- Claim: corpus Legal Surface Graph ----------------------------------- #
_B_LSG_REFERENCE_FAMILY_ONLY = _binding(
    "legal_surface_graph_v1_reference_family_only_v2_extends",
    kind="parser_incomplete",
    scope=(
        "The relation families merged into the corpus Legal Surface Graph: v1 "
        "merges the reference + anaphora lens edge families (the cross-statute "
        "refers_to / has_candidate backbone) only; the newer typed relation "
        "families (derivation edges, EU transposition edges, definition-use edges, "
        "dangling-reference status) are NOT merged in "
        "(lawvm.finland.legal_surface.corpus_graph)."
    ),
    effect="qualifies",
    expires_when=(
        "the corpus-graph build merges the typed v2 relation families "
        "(derivation / EU transposition / definition-use / dangling status) into "
        "the same cross-statute graph the reference backbone produces."
    ),
    public_message=(
        "The corpus Legal Surface Graph v1 carries the cross-statute reference "
        "backbone (refers_to / has_candidate) and the intra-statute structural "
        "edges of the reference + anaphora lenses. The newer typed relation "
        "families — derivation, EU transposition, definition-use, dangling status "
        "— are NOT yet merged into this graph; they are the DECLARED v2 extension."
    ),
)
_B_LSG_RESOLUTION_RECALL_BOUNDED = _binding(
    "legal_surface_graph_resolution_recall_bounded",
    kind="parser_incomplete",
    scope=(
        "The recall of cross-statute reference resolution in the corpus graph: an "
        "open / statute_only mention is left as-is (no promoted target), never an "
        "invented edge (lawvm.finland.legal_surface.corpus_graph)."
    ),
    effect="qualifies",
    expires_when=(
        "resolver coverage is extended such that a mention currently left "
        "open / statute_only becomes resolvable to a concrete provision target and "
        "promoted to a refers_to edge."
    ),
    public_message=(
        "Cross-statute reference resolution recall is BOUNDED. An open or "
        "statute_only mention is an honest non-promotion (no edge into a shared "
        "node), not a defect; an ambiguous resolution becomes a has_candidate, "
        "never an invented target — the graph never guesses a citation target."
    ),
)
_B_LSG_SURFACE_ONLY_AS_OF = _binding(
    "legal_surface_graph_surface_only_declared_slice_as_of",
    kind="doctrine_unresolved",
    scope=(
        "Whether the corpus Legal Surface Graph carries any legal-state or replay "
        "authority, and the slice/time it ranges over: it is surface_only, an "
        "as-of (surface_time) projection over a DECLARED corpus slice "
        "(lawvm.tools.corpus_surface_graph)."
    ),
    effect="outside_claim",
    expires_when=(
        "never for v0 — the corpus graph is a SURFACE projection by construction; "
        "every node/edge is minted surface_only through the assembler's "
        "firewall-enforcing path, and a legal/executable reading must leave the "
        "graph through a named authorization object (the authority firewall)."
    ),
    public_message=(
        "The corpus Legal Surface Graph is a SURFACE projection, never legal "
        "authority and never replay authorization. It is an as-of "
        "(surface_time) view over a DECLARED corpus slice (an explicit id list or "
        "a --limit prefix), not a whole-corpus or as-of-now guarantee."
    ),
)

#: The v0 binding set — EVERY V0_CLAIMS handle bound to a registered assumption.
V0_CLAIM_ASSUMPTION_BINDINGS: tuple[ClaimAssumptionBinding, ...] = (
    _B_BENCH_NOT_SOURCE_TRUTH,
    _B_BENCH_NOT_REPLAY_AUTH,
    _B_MAT_RELATIVE_ABSENCES,
    _B_MAT_SECTION_UNITS_ONLY,
    _B_REF_SURFACE_NOT_AUTHORITY,
    _B_REF_RECALL_BOUNDED,
    _B_KNOW_02_NOT_CHECKED,
    _B_KNOW_04_NOT_CHECKED,
    _B_KNOW_AVAILABLE_RECORDED,
    _B_TRANS_NOT_CONFORMANCE,
    _B_TRANS_DEADLINE_SEED,
    _B_TRANS_FI_ENACTMENT_CALLER,
    _B_DER_TEXTUAL_NOT_LINEAGE,
    _B_DER_MODEL_CODE_UNKNOWN,
    _B_DER_CONFORMANCE_CLAIMED,
    _B_CF_DEFINITION_USERS_SINGLE_HOP,
    _B_CF_CASCADE_BOUNDED_SEMANTIC_UNCOMPUTED,
    _B_CF_BARE_SECTION_BOUNDED,
    _B_XJUR_NOT_BUG_PORTABILITY,
    _B_XJUR_NOT_RECON_PARITY,
    _B_XJUR_SECTION_UNITS_ONLY,
    _B_DANGLING_AS_OF_NOW,
    _B_DANGLING_CORPUS_INCOMPLETE,
    _B_DANGLING_RESOLVED_SECTION_SCOPE,
    _B_FIXED_TERM_INCLUSIVE_EXCLUSIVE,
    _B_FIXED_TERM_AMBIGUOUS_STRICT,
    _B_FIXED_TERM_TEMPORARY_WARN,
    _B_TIMELINE_ROBUST_REPLAY_SEPARATE,
    _B_TIMELINE_DETECTION_NOT_CONCLUSION,
    _B_LSG_REFERENCE_FAMILY_ONLY,
    _B_LSG_RESOLUTION_RECALL_BOUNDED,
    _B_LSG_SURFACE_ONLY_AS_OF,
)


def _binding_index(
    bindings: Sequence[ClaimAssumptionBinding] = V0_CLAIM_ASSUMPTION_BINDINGS,
) -> dict[str, ClaimAssumptionBinding]:
    """Map handle → binding, rejecting a duplicate handle (declared once)."""
    index: dict[str, ClaimAssumptionBinding] = {}
    for binding in bindings:
        if binding.handle in index:
            raise ClaimAssumptionBindingError(
                f"duplicate binding handle {binding.handle!r} — a non-guarantee "
                f"handle is bound once"
            )
        index[binding.handle] = binding
    return index


def resolve_non_guarantee(
    handle: str,
    *,
    bindings: Sequence[ClaimAssumptionBinding] = V0_CLAIM_ASSUMPTION_BINDINGS,
) -> AssumptionRegister | None:
    """Resolve a declared non-guarantee handle to its registered assumption.

    Returns the bound :class:`AssumptionRegister` for ``handle``, or ``None`` if
    no binding declares it. A ``None`` is the per-handle coverage GAP: a claim
    declared the handle but no assumption is registered for it (the gate in
    :mod:`tests.test_claim_assumption_binding` FAILS on it). Resolution proves a
    declared assumption EXISTS — NOT that the boundary is complete or harmless.
    """
    binding = _binding_index(bindings).get(handle)
    return binding.assumption if binding is not None else None


def claim_assumption_binding_root(
    bindings: Sequence[ClaimAssumptionBinding] = V0_CLAIM_ASSUMPTION_BINDINGS,
) -> str:
    """``MapRoot`` over ``{handle: assumption_id}`` — the binding set committed.

    Adding, dropping, or editing any binding (or the body of a bound assumption,
    which moves its ``assumption_id``) changes the root, so the SET of declared
    handle→assumption bindings is itself a checkable, committed artifact — the
    same pattern as ``ClaimSurfaceManifest.manifest_root`` and
    ``assumption_register_root``. Empty is a valid deterministic (empty) root.
    """
    return map_root(
        _DOMAIN_CLAIM_ASSUMPTION_BINDING_ROOT,
        {b.handle: b.assumption_id for b in _binding_index(bindings).values()},
    )


def bound_handles(
    bindings: Sequence[ClaimAssumptionBinding] = V0_CLAIM_ASSUMPTION_BINDINGS,
) -> frozenset[str]:
    """The set of handles the binding set declares (for orphan/coverage checks)."""
    return frozenset(_binding_index(bindings))


__all__ = [
    "CLAIM_ASSUMPTION_BINDING_VERSION",
    "ClaimAssumptionBinding",
    "ClaimAssumptionBindingError",
    "V0_CLAIM_ASSUMPTION_BINDINGS",
    "bound_handles",
    "claim_assumption_binding_root",
    "resolve_non_guarantee",
]
