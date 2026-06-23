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
_B_CF_MULTIHOP_UNCOMPUTED = _binding(
    "counterfactual_multihop_and_semantic_uncomputed",
    kind="doctrine_unresolved",
    scope=(
        "Multi-hop citation cascades and semantic/temporal/transposition effects "
        "are uncomputed and declared in tier 3 "
        "(lawvm.tools.bill_counterfactual_effects)."
    ),
    effect="outside_claim",
    expires_when=(
        "a bounded, oracle-grounded multi-hop / semantic cascade computation exists "
        "that does not manufacture false precision; until then these classes are "
        "declared uncomputed in tier 3."
    ),
    public_message=(
        "Counterfactual effects compute direct (tier 1) and 1-hop resolved-citation "
        "(tier 2) effects only. Multi-hop cascades and semantic / temporal / "
        "transposition effects are DECLARED uncomputed in tier 3, never silently "
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
    _B_CF_MULTIHOP_UNCOMPUTED,
    _B_CF_BARE_SECTION_BOUNDED,
    _B_XJUR_NOT_BUG_PORTABILITY,
    _B_XJUR_NOT_RECON_PARITY,
    _B_XJUR_SECTION_UNITS_ONLY,
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
