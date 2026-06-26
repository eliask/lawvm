"""EXPERIMENTAL one-work certificate bundle writer (schema-pressure fixture).

Emits a complete ``lawvm.certificate.v0`` bundle directory for ONE Finnish
statute per notes/CERTIFICATE_SCHEMA_V0.md (spec_version 0.4.1) and
notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md (spec_version 0.3), within the
experimental-writer boundary of certificate spec §11.3:

* one Finnish legal work, ``closed_interval`` time scope, subsection or
  section granularity;
* all source bytes bundled from the local corpus (no URL-only references);
* one projection family: seam rows (``lawvm.provision_state.v1`` / seam
  spec 0.2);
* transitions are DERIVED FROM OBSERVED STATE DIFFS (the certificate spec
  §10 experimental carve-out) — the engine's covering-state evolution per
  change date, exactly the shape ``export_transition_graph`` computes.

THE OUTPUT IS A BUNDLE-WRITER FIXTURE, NOT A CHECKED CERTIFICATE. No checker
exists; nothing here emits or implies a ``VALID_*`` verdict. The writer-side
self-check (:func:`verify_bundle`) recomputes every committed root from the
bundle artifacts independently so the WRITER cannot ship an internally
inconsistent bundle — it is not checker v0 and asserts nothing beyond the
writer's own consistency. Bundles MUST NOT be published or presented as
checkable public claims (certificate spec §10, §11.3).

The interim decisions this writer surfaced on first emission were ratified
(or resolved) into certificate spec 0.4.1 §11.4; remaining engine-surface
limits are marked with ``SPEC-NOTE:`` comments referencing the exact spec
section.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec
from lawvm.core.provenance import SourceAnchor
from lawvm.core.stage_result import AuthoritySurface, StageResult
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string
from lawvm.tools.export_transition_graph import (
    DEFAULT_GRANULARITY,
    _canonical_statute_id,
    _engine_statute_id,
    _index_ops_by_date,
    _index_ops_by_expiry_date,
    _legal_op_summary,
    _ops_for_covering,
    covering_units,
    materialize_oracle_tree,
    run_engine_replay,
    structural_subtree_hash,
)

# ---------------------------------------------------------------------------
# Frozen identifiers (certificate spec §3.1.1, §6; trace spec §4, §5.2, §8.2)
# ---------------------------------------------------------------------------

CERTIFICATE_SCHEMA = "lawvm.certificate.v0"
CERTIFICATE_SPEC_VERSION = "0.4.1"
TRACE_SPEC_VERSION = "0.3"
SEAM_SPEC_VERSION = "0.2"
SEAM_SCHEMA = "lawvm.provision_state.v1"
PROFILE_ID = "fi.strict.current"
POLICY_ID = "lawvm.fi.default.v1"
HASH_PROFILE = "lawvm.hash.canonical_json.v1"
CHECKER_VERSION = "lawvm.checker.v0"
# BOOT-01 (Pro §3): honest name for the root set certificate_root commits — the
# FULL per-work temporal dossier root (all §3 subroots + policy-bindings trust
# root), NOT a sparse minimal-state pack.
CERTIFICATE_ROOT_PROFILE = "lawvm.certificate.full_work_dossier.v0"

D_CERT_ROOT = "lawvm.certificate.v0.root"
D_TRACE = "lawvm.certified_tree_transition_trace.v0"
D_TRANSITION = "lawvm.certified_tree_transition.v0"
D_SOURCE_BUNDLE = "lawvm.source_bundle.v0"
D_SOURCE_ARTIFACT = "lawvm.source_artifact.v0"
D_BASE_TREE = "lawvm.base_tree.v0"
D_CONTENT_BLOBS = "lawvm.content_blobs.v0"
D_CONTENT_BLOB = "lawvm.content_blob.v0"
D_STATE_ROOT = "lawvm.state_root.v0"
D_MATERIALIZATION = "lawvm.materialization_index.v0"
D_PROJECTION_PAYLOAD = "lawvm.projection_payload.v0"
D_PROJECTION_SEAM = "lawvm.projection.seam.v0"
D_PROJECTION_ROOT = "lawvm.projection_root.v0"
D_RESIDUAL_LEDGER = "lawvm.residual_ledger.v0"
D_FINDING_LEDGER = "lawvm.finding_ledger.v0"
D_SOURCE_UNIT_COVERAGE = "lawvm.source_unit_coverage.v0"
D_POTENTIAL_OP_COVERAGE = "lawvm.potential_operation_coverage.v0"
D_COVERAGE = "lawvm.coverage.v0"
D_STRICT_PROFILE = "lawvm.strict_profile.v0"
D_INTERPRETATION_POLICY = "lawvm.interpretation_policy.v0"
D_PROJECTION_SPECS = "lawvm.projection_specs.v0"
D_DIAGNOSTIC_REGISTRY = "lawvm.diagnostic_registry.v0"
D_CHECKER_CONTRACT = "lawvm.checker_contract.v0"
# BOOT-01 (audit-invariant registry; Pro §2/§8): the (code × profile →
# blocks/qualifies/permits) disposition matrix, rooted INDEPENDENTLY of the
# diagnostic-registry rows so the certification fold's defining policy input is
# content-bound on its own. The matrix is re-derived from the engine
# (FINDING_REGISTRY + the pinned strict-profile fields), never trusted from a
# row's asserted profile_disposition.
D_DISPOSITION_MATRIX = "lawvm.disposition_matrix.v0"
# BOOT-01: the policy-bindings object — aggregates the content-addressed roots
# of EVERY policy input the certification fold depends on into one object whose
# root is committed in the certificate root set. A forged registry/profile/
# disposition matrix changes a bound root → changes policy_bindings_root →
# changes certificate_root (detectable). See `build_policy_bindings`.
D_POLICY_BINDINGS = "lawvm.policy_bindings.v0"
# Certificate spec §2.1: change_dates_root is a VALUE SET — raw ISO date
# strings under this domain, the one named exception to the digest-member
# rule of §3.1.1.
D_CHANGE_DATES = "lawvm.change_dates.v0"

# StageResult endgame (WAIST #9): the per-stage account ledger. The dossier
# commits, ADDITIVELY beside the flat residual/finding/coverage roots, one
# subroot per pipeline stage's `core.stage_result.StageResult` account so a
# checker can attribute a divergence to a SPECIFIC stage. These roots aggregate
# the per-stage residual/finding/coverage subroots; they are NOT folded into the
# writer's flat §5.4 ledgers (which stay value-identical — 0-delta). See
# `core.stage_result_ledger` for the canonical row mapping.
D_STAGE_RESIDUAL_LEDGER = "lawvm.stage_residual_ledger.v0"
D_STAGE_FINDING_LEDGER = "lawvm.stage_finding_ledger.v0"
D_STAGE_COVERAGE = "lawvm.stage_coverage.v0"
D_STAGE_ACCOUNT = "lawvm.stage_account.v0"
D_STAGE_ACCOUNTS = "lawvm.stage_accounts.v0"
# WAIST #7 — the apply/replay execution-authority subroot. Additive: with an
# authorized replay (the green-corpus default) its single leaf is value-stable
# and it NEVER folds into the flat residual/finding/coverage roots, so those stay
# value-identical (0-delta). The checker can read WHICH replay was unauthorized.
D_APPLY_AUTHORITY = "lawvm.apply_authority.v0"

# Stage ids for the per-stage account ledger (WAIST #8/#9 live feeders).
STAGE_TIMELINE_MATERIALIZATION = "fi.timeline.materialization"
# StageResult endgame Wave-5: the remaining per-stage account ids routed
# into `stage_account_rows` so `verify_bundle` recomputes each one UNCONDITIONALLY
# (the WAIST #9 mechanism). All additive — 0-delta on the flat residual/finding/
# coverage roots; only `stage_accounts_root` gains members.
STAGE_SOURCE_IDENTITY = "fi.source.identity"  # #1
STAGE_STRUCTURE_WRITE_FOOTPRINT = "fi.structure.write_footprint"  # #3 (SEAM B)
STAGE_SOURCE_SYNTAX_FOREST = "fi.source_syntax.forest"  # #4 (SEAM A)
STAGE_LEGAL_SURFACE_GRAPH = "fi.legal_surface.graph"  # #5 (SEAM A)
STAGE_CANONICAL_OP_COMPILE = "fi.canonical_op.compile"  # #6 (SEAM B)
STAGE_PROJECTION_INTERLINKS = "fi.projection.interlinks"  # #10 (SEAM A)
STAGE_PROJECTION_OVERLAYS = "fi.projection.overlays"  # #10 (SEAM A)

# Certificate spec §3.5: bundle-local certificate-layer code (CERT.
# namespace) carried by kind=source_anchor_unavailable residuals (§5.4,
# trace spec §7).
SOURCE_ANCHOR_UNAVAILABLE_CODE = "CERT.SOURCE_ANCHOR_UNAVAILABLE"

# Bundle-local certificate-layer code (CERT. namespace) for the receipt
# consistency cross-check: a covering-state transition whose touched address
# is attributed to one or more landed ops, yet NO attributed op's WriteReceipt
# declares a footprint that explains the transition's target address. This is a
# genuine divergence between the two independent derivations — the per-date
# covering-frontier diff (what the certificate certifies) and the per-op landed
# receipts (what the apply boundary recorded). It is a NON-BLOCKING observation:
# it surfaces silent drift without softening the certificate or replacing either
# producer. Legitimate zero-receipt transitions (temporary-act lapse
# restorations, the enacted-base first materialization) carry no attributed ops
# and are NOT flagged — they are recorded as writer notes only.
RECEIPT_TRANSITION_DIVERGENCE_CODE = "CERT.RECEIPT_TRANSITION_DIVERGENCE"

# WAIST #7 firewall: a landed WriteReceipt carried into the dossier under a
# replay whose aggregate apply authority is NOT replay_authorized (a neutral /
# un-granted ExecutionAuthorization, or a write that does not stand under the
# conservative apply gate). This BLOCKS the clean claim: an unauthorized replay
# cannot masquerade as an authoritative receipt/dossier (Pro §8 the authority
# firewall; Pro §10 "clean claims forbidden when scoped blocking residue exists").
UNAUTHORIZED_REPLAY_RECEIPT_CODE = "CERT.UNAUTHORIZED_REPLAY_RECEIPT"

# Certificate spec §3.4 + §3.5: per-family run-provenance exclusion list.
# The seam payload's `engine` block (git commit/dirty/repository) is
# excluded from the projection-hash input — visible in the artifact row,
# never hashed (mirrors seam spec §3.1's derived_state_hash exclusion).
SEAM_HASH_EXCLUDED_MEMBERS: Tuple[str, ...] = ("engine",)

# Certificate spec §5.4 typed blocking fixed-term codes mapped to
# kind=expiry_unverified (matches seam spec 0.2 §6.1, including
# TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED).
_EXPIRY_BLOCKING_CODES = frozenset(
    {
        "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE",
        "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS",
        "TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS",
        "TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING",
        "TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED",
        "TEMPORAL.EVENT_BOUND_RESOLVER_MISSING",
        "TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE",
        "TEMPORAL.SOURCE_IMPOSSIBLE_DATE",
    }
)

# Certificate spec §11.3 alias-migration example: the renamed universal code
# carries its deprecated surface-lexeme alias in registry metadata.
_DEPRECATED_ALIASES: Dict[str, Tuple[str, ...]] = {
    "TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED": (
        "TEMPORAL.NON_VALIDITY_VOIMASSA_SUPPRESSED",
    ),
}

# StrictProfile channel gates: codes whose strict-profile disposition is
# governed by an explicit allows_* channel. When the channel is open the
# disposition softens from blocks to qualifies. (Writer-side approximation of
# the engine's verdict-rail composition; see module report.)
_PROFILE_GATED_CODES: Dict[str, str] = {
    "TIME.ESTIMATED_EFFECTIVE_DATE": "allows_estimated_dates",
    "PARSE.TARGET_GUESSING": "allows_target_guessing",
    "ELAB.OMISSION_EXPANSION": "allows_omission_expansion",
    "APPLY.UNCOVERED_BODY_RECOVERY": "allows_uncovered_body_recovery",
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": "allows_fallback_whole_section_replace",
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": "allows_context_dependent_anchor_resolution",
    "APPLY.WORD_SUBSTITUTION": "allows_word_substitution",
    "APPLY.SOURCE_CORRECTED_BY_PATCH": "allows_source_correction_rules",
}

# BOOT-01: bundle-local diagnostic codes (outside FINDING_REGISTRY) carry their
# canonical role here so the disposition matrix can derive their disposition by
# the SAME _profile_disposition rule the engine codes use — the disposition is
# never a row-authored literal. SOURCE_ANCHOR_UNAVAILABLE is special-cased in
# _profile_disposition (qualifies); RECEIPT_TRANSITION_DIVERGENCE is an
# observation (permits).
_BUNDLE_LOCAL_CODE_ROLES: Dict[str, str] = {
    SOURCE_ANCHOR_UNAVAILABLE_CODE: "obligation",
    RECEIPT_TRANSITION_DIVERGENCE_CODE: "observation",
}

_RESIDUAL_KINDS = (
    "expiry_unverified",
    "failed_operation",
    "manual_frontier",
    "source_pathology",
    "grounding_unclassified",
    "quirks_recovery",
    "unsupported_scoped_expiry",
    "source_anchor_unavailable",
)


class BundleSpecError(ValueError):
    """A spec rule was violated while constructing bundle artifacts."""


class BundleSelfCheckError(AssertionError):
    """Writer-side self-check failed: a recomputed root or status disagrees.

    This is the WRITER's own consistency gate, not a checker verdict.
    """


# ---------------------------------------------------------------------------
# Canonical hash profile (certificate spec §3.1) and root constructors
# (certificate spec §3.1.1) — frozen
# ---------------------------------------------------------------------------


def canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8 bytes of the canonical JSON encoding (§3.1 frozen profile)."""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _rendered(digest: "hashlib._Hash") -> str:
    return "sha256:" + digest.hexdigest()


def leaf_hash(domain: str, obj: Any) -> str:
    """``LeafHash(domain, obj)`` per certificate spec §3.1.1."""
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b"\x00")
    h.update(canonical_json_bytes(obj))
    return _rendered(h)


def list_root(domain: str, ordered_leaf_hashes: Sequence[str]) -> str:
    """``ListRoot(domain, ordered)`` per §3.1.1. Duplicate leaves are INVALID."""
    ordered = list(ordered_leaf_hashes)
    if len(set(ordered)) != len(ordered):
        raise BundleSpecError(f"duplicate leaf under ListRoot({domain!r}) — INVALID per spec §3.1.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":list\x00")
    h.update(canonical_json_bytes(ordered))
    return _rendered(h)


def set_root(domain: str, leaf_hashes: Iterable[str]) -> str:
    """``SetRoot(domain, leaves)`` per §3.1.1. Duplicate leaves are INVALID."""
    leaves = sorted(leaf_hashes)
    for a, b in zip(leaves, leaves[1:], strict=False):
        if a == b:
            raise BundleSpecError(f"duplicate leaf under SetRoot({domain!r}) — INVALID per spec §3.1.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":set\x00")
    h.update(canonical_json_bytes(leaves))
    return _rendered(h)


def _sha256_rendered(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# StageResult endgame (WAIST #9): per-stage account subroots.
#
# Each pipeline stage's `core.stage_result.StageResult` account maps (via the
# canonical `core.stage_result_ledger` row mapping) onto three subroots built
# with the EXISTING leaf_hash/set_root vocabulary — no new hash machinery:
#
#   residual_subroot = set_root(D_STAGE_RESIDUAL_LEDGER, [leaf_hash(...) per row])
#   finding_subroot  = set_root(D_STAGE_FINDING_LEDGER,  [leaf_hash(...) per row])
#   coverage_subroot = leaf_hash(D_STAGE_COVERAGE, coverage_row)
#
# A per-stage `stage_account_root` commits the three together with the stage id;
# the aggregate `stage_accounts_root` is the set_root over the per-stage account
# roots. This is the additive attribution layer: with ZERO stages it is the
# empty set_root (a stable constant) and it never perturbs the flat
# residual/finding/coverage roots.
# ---------------------------------------------------------------------------


def stage_residual_subroot(rows: Sequence[Mapping[str, Any]]) -> str:
    """Subroot over one stage's canonical residual rows (order-independent set).

    ``StageResult``/``Residual`` permit duplicate residual records (no dedup), and
    ``residual_row`` drops distinguishing context, so two residuals differing only
    in dropped context map to identical rows. ``set_root`` has SET semantics — a
    repeated leaf is the same set member — so the leaf-hash list is de-duplicated
    before ``set_root`` (which rejects duplicate leaves). The committed root is
    unchanged when no duplicates exist (0-delta); without dedup an identical-row
    duplicate would crash the whole certificate build.
    """
    leaves = list(dict.fromkeys(leaf_hash(D_STAGE_RESIDUAL_LEDGER, row) for row in rows))
    return set_root(D_STAGE_RESIDUAL_LEDGER, leaves)


def stage_finding_subroot(rows: Sequence[Mapping[str, Any]]) -> str:
    """Subroot over one stage's canonical finding rows (order-independent set).

    Findings can likewise collapse to identical canonical rows; ``set_root`` is a
    SET so the leaf-hash list is de-duplicated first (0-delta when no duplicates,
    no crash when there are).
    """
    leaves = list(dict.fromkeys(leaf_hash(D_STAGE_FINDING_LEDGER, row) for row in rows))
    return set_root(D_STAGE_FINDING_LEDGER, leaves)


def stage_coverage_subroot(row: Mapping[str, Any]) -> str:
    """Subroot over one stage's canonical coverage row (single leaf)."""
    return leaf_hash(D_STAGE_COVERAGE, row)


def stage_account_root(account_row: Mapping[str, Any]) -> str:
    """Root committing one stage's three subroots + its stage id (a leaf)."""
    return leaf_hash(
        D_STAGE_ACCOUNT,
        {
            "stage": account_row["stage"],
            "residual_subroot": account_row["residual_subroot"],
            "finding_subroot": account_row["finding_subroot"],
            "coverage_subroot": account_row["coverage_subroot"],
        },
    )


def build_stage_account_row(stage_id: str, stage: StageResult[Any]) -> Dict[str, Any]:
    """Project ONE StageResult onto its committed stage-account row.

    The row carries the canonical residual/finding/coverage rows (so the artifact
    is self-evidencing — a checker reads WHAT was accounted, not just a hash) plus
    the three subroots and the per-stage account root the aggregate folds.
    """
    from lawvm.core.stage_result_ledger import (
        stage_coverage_row,
        stage_finding_rows,
        stage_residual_rows,
    )

    residual_rows = stage_residual_rows(stage)
    finding_rows = stage_finding_rows(stage)
    coverage = stage_coverage_row(stage)
    residual_subroot = stage_residual_subroot(residual_rows)
    finding_subroot = stage_finding_subroot(finding_rows)
    coverage_subroot = stage_coverage_subroot(coverage)
    row: Dict[str, Any] = {
        "stage": stage_id,
        "residual_rows": residual_rows,
        "finding_rows": finding_rows,
        "coverage_row": coverage,
        "residual_subroot": residual_subroot,
        "finding_subroot": finding_subroot,
        "coverage_subroot": coverage_subroot,
    }
    row["stage_account_root"] = stage_account_root(row)
    return row


def stage_accounts_root(account_rows: Sequence[Mapping[str, Any]]) -> str:
    """Aggregate root over the per-stage account roots (the additive layer).

    Order-independent set over the stage account roots. With zero stages this is
    the empty set_root under D_STAGE_ACCOUNTS — a stable constant — so an empty
    stage-account ledger never perturbs anything.
    """
    return set_root(
        D_STAGE_ACCOUNTS, [stage_account_root(row) for row in account_rows]
    )


def _verify_coverage_row_arithmetic(stage: str, coverage_row: Mapping[str, Any]) -> None:
    """Re-derive the coverage partition FROM the committed counts, not its hash.

    ``stage_coverage_subroot`` only proves the committed ``coverage_row`` hashes to
    its committed ``coverage_subroot`` (circular self-consistency). It never checks
    that the four classes actually sum to ``total`` nor that the committed
    ``is_partition`` matches the counts. So a forged row like ``{total:999,
    owned:0, benign:0, residual:0, violation:0, is_partition:true}`` would pass the
    hash recompute untouched. This re-derives both verdicts from the counts:

    * when totality is claimed, ``owned+benign+residual+violation`` MUST equal
      ``total`` (no leaked, unaccounted units);
    * the committed ``is_partition`` MUST equal the recomputed
      ``totality_claimed and partition_total == total`` (mirrors
      :meth:`CoverageCertificate.is_partition`).
    """
    owned = int(coverage_row.get("owned", 0))
    benign = int(coverage_row.get("benign", 0))
    residual = int(coverage_row.get("residual", 0))
    violation = int(coverage_row.get("violation", 0))
    total = int(coverage_row.get("total", 0))
    totality_claimed = bool(coverage_row.get("totality_claimed", False))
    partition_total = owned + benign + residual + violation
    if totality_claimed:
        _require(
            partition_total == total,
            f"stage {stage!r} coverage claims totality but its classes "
            f"({owned}+{benign}+{residual}+{violation}={partition_total}) do not "
            f"sum to total {total}",
        )
    recomputed_is_partition = totality_claimed and partition_total == total
    _require(
        bool(coverage_row.get("is_partition", False)) == recomputed_is_partition,
        f"stage {stage!r} coverage is_partition "
        f"{bool(coverage_row.get('is_partition', False))!r} does not recompute "
        f"from its counts (expected {recomputed_is_partition!r})",
    )


def _verify_stage_accounts(account_rows: Sequence[Mapping[str, Any]]) -> str:
    """Recompute the per-stage subroots FROM the committed rows and re-aggregate.

    The writer-side self-check consumer (`verify_bundle`): a stage account that
    was dropped/severed before the dossier, or whose committed subroot disagrees
    with its committed rows, makes this recompute diverge from the committed
    `stage_accounts_root` (BundleSelfCheckError) — the guard-liveness property.

    The coverage check is two-layered: the subroot recompute proves the committed
    coverage_row hashes to its committed coverage_subroot (self-consistency), and
    :func:`_verify_coverage_row_arithmetic` re-derives the partition/totality
    verdict from the counts so a forged row with inconsistent counts cannot pass.
    """
    for row in account_rows:
        _require(
            stage_residual_subroot(row["residual_rows"]) == row["residual_subroot"],
            f"stage {row['stage']!r} residual_subroot does not recompute from its rows",
        )
        _require(
            stage_finding_subroot(row["finding_rows"]) == row["finding_subroot"],
            f"stage {row['stage']!r} finding_subroot does not recompute from its rows",
        )
        _require(
            stage_coverage_subroot(row["coverage_row"]) == row["coverage_subroot"],
            f"stage {row['stage']!r} coverage_subroot does not recompute from its row",
        )
        _verify_coverage_row_arithmetic(row["stage"], row["coverage_row"])
        _require(
            stage_account_root(row) == row["stage_account_root"],
            f"stage {row['stage']!r} stage_account_root does not recompute from its subroots",
        )
    return stage_accounts_root(account_rows)


# StageResult endgame Wave-5 (ORCH-2): the stages whose BLOCKING residual
# contributes to the certificate status (the (C) closure — a broken-ref #5 or a
# dropped-universe-member #10 forces blocked). Their (D)-routed account is the
# reachable, tamper-checkable carrier of that blocking signal.
STATUS_CONTRIBUTING_STAGE_IDS = frozenset(
    {
        STAGE_LEGAL_SURFACE_GRAPH,
        STAGE_PROJECTION_INTERLINKS,
        STAGE_PROJECTION_OVERLAYS,
    }
)


def _committed_stage_blocking_residual_count(
    account_rows: Sequence[Mapping[str, Any]],
) -> int:
    """Count blocking residuals in the (C)-contributing committed stage rows.

    The verify-side recompute of :func:`_stage_blocking_residual_count`: it reads
    the COMMITTED ``residual_rows`` of the #5/#10 stage accounts (which
    ``_verify_stage_accounts`` has already proven recompute from their subroots) and
    counts the blocking ones. A tampered/forged #5/#10 row that drops a blocking
    residual changes the stage subroot (caught by ``_verify_stage_accounts``); a row
    that legitimately carries one forces ``blocked`` here — the genuine (C) bite.
    """
    count = 0
    for row in account_rows:
        if row.get("stage") not in STATUS_CONTRIBUTING_STAGE_IDS:
            continue
        count += sum(1 for r in row.get("residual_rows", []) if bool(r.get("blocking")))
    return count


def _fi_materialization_stage(bundle: Any, as_of: str) -> "StageResult[Any]":
    """Return the FI PIT materialization `StageResult` for one boundary date.

    Mirrors `materialize_fi_transition_graph_tree` but keeps the FULL replay
    products so the carried `materialization_stage` (StageResult endgame WAIST #8)
    reaches the dossier instead of being discarded with everything-but-`.ir`. The
    coverage is the FI replay's own account (same materialization the rest of the
    bundle is built from) — no independent re-materialization that could diverge.
    """
    from lawvm.finland.replay_products import build_replay_products

    result = bundle.result
    products = build_replay_products(
        ctx=result.ctx,
        statute_id=bundle.engine_id,
        replay_fold_state=result.products.replay_fold_state,
        lo_ops_out=bundle.lo_ops,
        as_of=as_of,
        expires_as_of=as_of,
        synthesize_repeal_placeholders=True,
        temporal_events=result.products.temporal_events,
        migration_events=result.products.migration_events,
        fold_backfill_preview_cache=bundle.materialization_cache,
    )
    stage = products.materialization_stage
    if stage is None:
        raise BundleSpecError(
            "FI replay produced no materialization StageResult account for the "
            f"certificate date {as_of!r}; the materialization coverage cannot be "
            "routed into the dossier (the experimental writer requires the "
            "StageResult-carried materialization, not the discarding path)"
        )
    return stage


def _materialization_coverage_violation(coverage_row: Mapping[str, Any]) -> int:
    """The committed unowned-signal violation count for a materialization stage."""
    return int(coverage_row.get("violation", 0))


def _require_clean_materialization_stage(stage: "StageResult[Any]") -> None:
    """Self-check: a degraded/violating materialization forbids a clean cert.

    The load-bearing branch (WAIST #8): the certificate claims totality over a
    fully-materialized work. If the materialization coverage carries an
    unowned-signal violation (e.g. `degraded_missing_scope`) or a blocking
    residual, the writer MUST refuse to emit a clean dossier. RED if the
    coverage is severed back to the plain discarding path (the account would be
    the identity-clean default and this guard could never fire).
    """
    _require(
        stage.coverage.violation == 0,
        "materialization coverage carries "
        f"{stage.coverage.violation} unowned-signal violation(s); a clean "
        "certificate cannot be claimed over a degraded materialization",
    )
    _require(
        not stage.has_blocking_residual,
        "materialization stage carries a blocking residual; a clean certificate "
        "cannot be claimed over a degraded materialization",
    )


def _require_monotone_stage_residual_ledger(
    stage_account_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Self-check (EV-03): no residual silently vanished across the per-stage fold.

    Drives the production EV-03 sweep over the committed per-stage account rows: a
    residual COUNTED in a stage's coverage ``violation`` class must have ≥1 BLOCKING
    residual record committed in that stage's ledger (and the dual). A non-monotone
    account is a producer/aggregator defect (a counted residual the ledger dropped,
    or a recorded residual the count forgot), never a corpus fact — so the writer
    refuses to emit a dossier over it rather than silently certifying a torn ledger.
    On the green corpus every stage carries ``violation == 0`` and no blocking
    residual → the sweep is empty (0-delta).
    """
    from lawvm.core.stage_residual_monotonicity import sweep_stage_residual_ledger

    findings = sweep_stage_residual_ledger(stage_account_rows)
    if findings:
        detail = "; ".join(f.detail for f in findings)
        raise BundleSpecError(
            "per-stage residual ledger is non-monotone (EV-03): " + detail
        )


def _require_self_evidencing_stage_residuals(
    stage_account_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Self-check (EV-07): every committed source-text-failure residual carries its snippet.

    Sweeps the committed per-stage residual rows (each a
    :func:`lawvm.core.stage_result_ledger.residual_row` projection carrying ``kind`` +
    ``source_text``): a row whose ``kind`` is in the source-text-failure family
    (``unowned_violation`` / ``typed_residual``) with an empty ``source_text`` is an
    opaque diagnostic about unhandled source text. The writer refuses to emit a
    dossier carrying a snippet-less source-text-failure residual rather than
    committing an un-auditable ledger. On the green corpus the forest/surface
    producers set the verbatim text by construction → silent (0-delta).
    """
    from lawvm.core.diagnostic_self_evidencing import (
        DIAGNOSTIC_NOT_SELF_EVIDENCING,
        SOURCE_TEXT_FAILURE_KINDS,
    )

    offenders: List[str] = []
    for account_row in stage_account_rows:
        stage = str(account_row.get("stage", ""))
        for residual_row_dict in account_row.get("residual_rows", ()) or ():
            kind = str(residual_row_dict.get("kind", ""))
            if kind not in SOURCE_TEXT_FAILURE_KINDS:
                continue
            if str(residual_row_dict.get("source_text", "") or "").strip():
                continue
            offenders.append(
                f"stage {stage!r} residual (kind={kind!r}, "
                f"reason={residual_row_dict.get('reason', '')!r}) carries no snippet"
            )
    if offenders:
        raise BundleSpecError(
            f"{DIAGNOSTIC_NOT_SELF_EVIDENCING}: source-text-failure residual(s) "
            "without a verbatim offending snippet (not self-evidencing): "
            + "; ".join(offenders)
        )


def _verify_materialization_stage_clean(stage_account_rows: Sequence[Mapping[str, Any]]) -> None:
    """`verify_bundle` consumer: re-assert the materialization branch from rows.

    The writer-side recompute of :func:`_require_clean_materialization_stage` over
    the COMMITTED stage-account rows. A clean certificate MUST carry the
    materialization stage account with a clean coverage (no unowned-signal
    violation, no blocking residual). If the materialization coverage was severed
    before the dossier (the discarding path), the account is absent — also a
    failure here, so the un-sever cannot regress silently.
    """
    materialization_account = next(
        (
            row
            for row in stage_account_rows
            if row.get("stage") == STAGE_TIMELINE_MATERIALIZATION
        ),
        None,
    )
    _require(
        materialization_account is not None,
        "clean certificate is missing the "
        f"{STAGE_TIMELINE_MATERIALIZATION!r} stage account (the materialization "
        "coverage was not routed into the dossier)",
    )
    assert materialization_account is not None
    coverage_row = materialization_account["coverage_row"]
    _require(
        _materialization_coverage_violation(coverage_row) == 0,
        "clean certificate carries a materialization coverage with "
        f"{_materialization_coverage_violation(coverage_row)} unowned-signal "
        "violation(s); a degraded materialization cannot be certified clean",
    )
    _require(
        not any(bool(r.get("blocking")) for r in materialization_account["residual_rows"]),
        "clean certificate carries a blocking materialization residual; a "
        "degraded materialization cannot be certified clean",
    )


# ---------------------------------------------------------------------------
# WAIST #7 — the apply/replay execution-authority firewall at the certificate.
# ---------------------------------------------------------------------------


def _fi_apply_authority(bundle: Any) -> "AuthoritySurface":
    """The per-replay apply execution authority for this dossier (firewall input).

    READS the type-carried ``ReplayProducts.apply_authority`` (WAIST #7), which the
    replay assembly minted via :func:`aggregate_replay_authority` over the landed
    write receipts + findings. The carrier is load-bearing: a clean dossier's
    firewall stands on the authority the producer carried, not a cert-side
    re-derivation (the re-derivation duplication the exit re-audit flagged).

    When the carrier is absent (a replay path that never set it — never the green
    corpus default), FALL BACK to the descriptive re-derivation from
    ``bundle.result`` so the writer still never trusts an un-set carrier. Both
    compute the IDENTICAL conjunction over the same receipts/findings, so on a
    faithful replay the carried value and the fallback agree byte-for-byte (0-delta).
    """
    from lawvm.finland.apply_replay_authorization import aggregate_replay_authority

    result = bundle.result
    carried = getattr(result.products, "apply_authority", None)
    if carried is not None:
        return carried
    return aggregate_replay_authority(
        write_receipts=result.write_receipts,
        findings=result.findings,
    )


# ---------------------------------------------------------------------------
# StageResult endgame Wave-5: the SEAM-A cert-side stage feeders. Each
# builds ONE StageResult account from artifacts the cert already holds (the
# enacted body bytes / the corpus store) and routes it into `stage_account_rows`
# so `_verify_stage_accounts` recomputes it UNCONDITIONALLY (checkable on the
# BLOCKED 482/2024). All additive read-offs of accounts the value path already
# produces — 0-delta on the flat roots.
# ---------------------------------------------------------------------------


def _fi_source_identity_stage(
    source_identities: Sequence[Mapping[str, Any]],
) -> "StageResult[None]":
    """Source-identity stage account (WAIST #1) from the witnessed reads in hand.

    Built from the SAME content-witnessed source identities the cert already read
    + content-hashed into ``source_bundle_root`` (no second read — 0-delta). The
    coverage is a single-class ``source_artifacts`` partition: every bundled source
    was read and content-addressed (``owned`` = number of bundled sources, zero
    residual/violation). The witness DIGESTS keep their unconditional content check
    on ``source_bundle_root`` (ORCH-3: no ledger evidence extension); this stage row
    is the per-stage checkable attribution the (D) goal demands.
    """
    from lawvm.core.stage_result import CoverageCertificate, StageResult

    total = len(source_identities)
    return StageResult(
        value=None,
        coverage=CoverageCertificate(
            unit="source_artifacts",
            total=total,
            owned=total,
            totality_claimed=True,
        ),
    )


def _fi_source_syntax_stage(
    surface_bundle: Any,
) -> "StageResult[Any]":
    """Source-syntax forest stage account (WAIST #4, SEAM A) over the #2 bundle.

    Mirrors :func:`graph_build._gate_forest_coverage` exactly: for each unit of the
    already-built #2 ``SourceSurfaceBundle`` assemble the forest as a typed
    StageResult and aggregate the token-partition accounts. For the v0 single
    whole-body unit this is a pure read-off of the SAME forest ``_gate_forest_coverage``
    builds — 0-delta. ``violation>0`` ⟺ a silent-unowned span; on the green corpus
    it is 0 (the residue rides as non-blocking ``typed_residual``). Per the #4 §LEDGER
    row the forest violation is the established totality-scoped frontier (NOT a
    clean-cert (C) gap), so this routes its account ONLY.
    """
    from lawvm.core.stage_result import (
        NEUTRAL_AUTHORITY,
        CoverageCertificate,
        StageResult,
    )
    from lawvm.finland.legal_surface.source_syntax_graph import (
        assemble_source_syntax_graph_staged,
    )

    owned = benign = residual_n = violation = 0
    residuals: list[Any] = []
    last_value: Any = None
    for unit in surface_bundle.units:
        forest_stage = assemble_source_syntax_graph_staged(
            subject=surface_bundle.subject, unit=unit
        )
        cov = forest_stage.coverage
        owned += cov.owned
        benign += cov.benign
        residual_n += cov.residual
        violation += cov.violation
        residuals.extend(forest_stage.residuals)
        last_value = forest_stage.value
    coverage = CoverageCertificate(
        unit="tokens",
        total=owned + benign + residual_n + violation,
        owned=owned,
        benign=benign,
        residual=residual_n,
        violation=violation,
        totality_claimed=True,
    )
    return StageResult(
        value=last_value,
        residuals=tuple(residuals),
        findings=(),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def _fi_legal_surface_graph_stage(body_bytes: bytes, statute_id: str) -> "StageResult[Any]":
    """Legal Surface Graph stage account (WAIST #5, SEAM A) from the enacted body.

    Calls the staged producer over the enacted body bytes the cert already bundled
    (``source_blobs[base_artifact_id]``). The ``value`` is byte-identical to the
    value-only ``build_legal_surface_graph``; the account carries the surface-node
    four-class partition + per-non-owned-node residuals (a ``broken`` ref →
    BLOCKING ``unowned_violation``). 0-delta D-routing; its blocking residual ALSO
    contributes to ``compute_certificate_status`` so a broken ref forces blocked
    (PART 2 status-contribution, reachable — NOT a clean-gated firewall).
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph_staged

    return build_legal_surface_graph_staged(body_bytes, statute_id)


def _fi_projection_stage(
    project_fn: Any, statute_id: str, corpus: Any
) -> "StageResult[None]":
    """Projection stage account (WAIST #10, SEAM A) wrapping a ``FiProjectionResult``.

    ``project_fn`` is ``_project_interlinks_for_statute`` or
    ``_project_overlays_for_statute``. The producer returns a ``FiProjectionResult``
    (a ``PartitionResult`` with ``.coverage``/``.residuals``); wrap it into a
    ``StageResult`` so ``build_stage_account_row`` can project it (ORCH-5: both
    interlinks + overlays are routed). A silently-dropped universe member rides as a
    BLOCKING ``projection_residual`` (``coverage.violation>0``); that blocking
    residual ALSO contributes to ``compute_certificate_status`` (PART 2). On
    482/2024 the statute XML is present → ``violation==0`` → 0-delta.
    """
    from lawvm.core.stage_result import NEUTRAL_AUTHORITY, StageResult

    proj = project_fn(statute_id, corpus)
    return StageResult(
        value=None,
        residuals=tuple(proj.residuals),
        findings=(),
        coverage=proj.coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def _stage_blocking_residual_count(stages: Sequence[Tuple[str, "StageResult[Any]"]]) -> int:
    """Count blocking residuals across the (C)-contributing stages (#5, #10).

    PART 2 status-contribution (ORCH-2): a #5 broken-ref / #10 dropped-universe-
    member BLOCKING residual must UNCONDITIONALLY force ``certificate_status=blocked``
    (reachable, genuine (C) — NOT a clean-gated firewall that never fires on FI). The
    count is passed to :func:`compute_certificate_status` as EXTRA blocking residue;
    it does NOT fold into the flat ``residual_root`` (which stays value-identical), so
    on the green corpus (every contributing stage's ``violation==0``, no blocking
    residual) the count is 0 → 0-delta (every FI cert is already ``blocked`` from its
    ledger residue; a non-zero count here keeps a blocked cert blocked, never flips a
    clean one — if it did, that is a NEW-CORRECT finding).
    """
    count = 0
    for _stage_id, stage in stages:
        count += sum(1 for r in stage.residuals if getattr(r, "blocking", False))
    return count


def apply_authority_row(authority: "AuthoritySurface") -> Dict[str, Any]:
    """Project the per-replay :class:`AuthoritySurface` onto a committed row.

    Self-evidencing: it carries the typed authorization fields (a checker reads
    WHY the replay was/was not authorized), not just a flag.
    """
    authorization = authority.authorization
    return {
        "replay_authorized": bool(authority.replay_authorized),
        "is_neutral": bool(authority.is_neutral),
        "authorization": authorization.to_dict() if authorization is not None else None,
    }


def apply_authority_root(authority: "AuthoritySurface") -> str:
    """Subroot over the single per-replay apply-authority row (additive layer).

    A single leaf under ``D_APPLY_AUTHORITY``; with an authorized replay (the
    green-corpus default) it is value-stable and never folds into the flat
    residual/finding/coverage roots (0-delta).
    """
    return leaf_hash(D_APPLY_AUTHORITY, apply_authority_row(authority))


def _require_authorized_replay(authority: "AuthoritySurface") -> None:
    """Writer-side firewall (WAIST #7): an unauthorized replay forbids a clean cert.

    The load-bearing branch: a clean/authoritative dossier requires the
    per-replay apply authority to be ``replay_authorized`` (granted by an explicit
    :class:`ExecutionAuthorization`). A neutral / un-granted surface, or a replay
    that landed a write outside the conservative apply gate, makes a clean claim
    impossible — "an unauthorized replay cannot produce an authoritative receipt".
    RED if the authority is severed back to the neutral default (it would be
    ``replay_authorized=False`` and this guard fires) or if the gate is wired to
    ignore it.
    """
    _require(
        authority.replay_authorized,
        "apply/replay authority is not replay_authorized (the per-replay "
        "ExecutionAuthorization does not grant replay authority); a clean "
        "certificate cannot be claimed over an unauthorized replay — an "
        "unauthorized replay cannot produce an authoritative receipt",
    )


def _verify_apply_authority_clean(
    apply_authority_rows: Sequence[Mapping[str, Any]],
) -> None:
    """`verify_bundle` consumer: re-assert the firewall from the COMMITTED row.

    The writer-side recompute of :func:`_require_authorized_replay` over the
    committed apply-authority row. A clean certificate MUST carry exactly one
    apply-authority row whose ``replay_authorized`` is True. If the row was
    severed before the dossier (the authority dropped to neutral), the row is
    absent or non-authorized — a failure here, so the firewall cannot regress
    silently. Raises :class:`BundleSelfCheckError`.
    """
    _require(
        len(apply_authority_rows) == 1,
        "clean certificate must carry exactly one apply-authority row "
        f"(found {len(apply_authority_rows)}); the apply/replay authority was not "
        "routed into the dossier",
    )
    row = apply_authority_rows[0]
    _require(
        bool(row.get("replay_authorized")) is True,
        "clean certificate carries an apply-authority row that is NOT "
        "replay_authorized; an unauthorized replay cannot be certified clean "
        "(an unauthorized replay cannot produce an authoritative receipt)",
    )


def projection_hash_view(payload: Mapping[str, Any], excluded_members: Sequence[str]) -> Dict[str, Any]:
    """Certificate spec §3.4 hash view: payload minus declared run-provenance.

    Excluded members stay VISIBLE in the emitted artifact row; only the hash
    input drops them, so engine commit/dirty-state churn cannot reach
    ``projection_hash`` or ``certificate_root``.
    """
    excluded = set(excluded_members)
    return {k: v for k, v in payload.items() if k not in excluded}


def projection_payload_hash(payload: Mapping[str, Any], excluded_members: Sequence[str]) -> str:
    """``projection_hash`` per certificate spec §3.4 (hash-view normalized)."""
    return leaf_hash(D_PROJECTION_PAYLOAD, projection_hash_view(payload, excluded_members))


def _plainify(value: Any, path: str = "") -> Any:
    """Convert frozen mappings/tuples from engine carriers into plain JSON values."""
    if isinstance(value, Mapping):
        return {str(k): _plainify(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plainify(v, f"{path}[]") for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise BundleSpecError(f"non-JSON value of type {type(value).__name__} at {path or '<root>'}")


# ---------------------------------------------------------------------------
# Diagnostic registry manifest (certificate spec §3.5)
# ---------------------------------------------------------------------------


def _residual_kind_for_code(code: str, spec: Optional[FindingSpec]) -> str:
    """Map a diagnostic code to its §5.4 residual kind (total, deterministic)."""
    if code == SOURCE_ANCHOR_UNAVAILABLE_CODE:
        return "source_anchor_unavailable"
    if code in _EXPIRY_BLOCKING_CODES:
        return "expiry_unverified"
    if code == "TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED":
        return "unsupported_scoped_expiry"
    if code == "APPLY.FAILED_OPERATION":
        return "failed_operation"
    if spec is None:
        return "grounding_unclassified"
    if spec.family == "source_pathology":
        return "source_pathology"
    if spec.family == "recovery":
        return "quirks_recovery"
    if spec.family == "violation":
        return "failed_operation"
    if spec.family == "ambiguity":
        # Certificate spec §5.4: non-expiry blocking ambiguity findings
        # (e.g. TIME.TRIGGER_COVERAGE_INCOMPLETE) map to manual_frontier —
        # resolution awaits external/manual input.
        return "manual_frontier"
    return "grounding_unclassified"


def _profile_disposition(code: str, spec: Optional[FindingSpec], profile_fields: Mapping[str, Any]) -> str:
    """Derive the (code, fi.strict.current) disposition: blocks/qualifies/permits."""
    if code == SOURCE_ANCHOR_UNAVAILABLE_CODE:
        # Diff-derived experimental transitions carry no byte anchors; the
        # gap qualifies the asserted state — it never reads as clean.
        return "qualifies"
    if code == RECEIPT_TRANSITION_DIVERGENCE_CODE:
        # Bundle-local non-blocking observation (role=observation -> permits);
        # derived here so its disposition is never a row-authored literal.
        return "permits"
    if spec is None:
        raise BundleSpecError(f"unregistered diagnostic code {code!r} has no derivable disposition")
    gate = _PROFILE_GATED_CODES.get(code)
    if gate is not None and bool(profile_fields.get(gate, False)):
        return "qualifies"
    if spec.role == "violation":
        return "blocks"
    if spec.role == "obligation":
        if spec.default_enforcement in ("hard_fail", "strict_fail"):
            return "blocks"
        return "qualifies"
    # observation (and anything informational)
    return "permits"


def build_disposition_matrix(profile_fields: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    """The canonical (code -> {profile_id: disposition}) matrix (BOOT-01).

    Derived from the engine ONLY — ``FINDING_REGISTRY`` plus the two bundle-local
    codes — evaluated through ``_profile_disposition`` under the pinned
    strict-profile fields. This is the SINGLE authority for a code's
    blocks/qualifies/permits disposition; the diagnostic-registry rows and the
    residual ``profile_effect`` copies both derive from it (Pro §8 item B: a row
    never author-controls its own disposition). The matrix is content-bound via
    :func:`disposition_matrix_root` into the policy-bindings object so a forged
    registry that flips a disposition cannot recompute clean (Pro §2 forge).
    """
    matrix: Dict[str, Dict[str, str]] = {}
    for code in sorted(FINDING_REGISTRY):
        spec = FINDING_REGISTRY[code]
        if spec.role == "barrier":
            # §3.5: barrier roles can never produce a runtime finding -> excluded
            # from the registry, so excluded from the matrix.
            continue
        matrix[code] = {PROFILE_ID: _profile_disposition(code, spec, profile_fields)}
    for code in _BUNDLE_LOCAL_CODE_ROLES:
        matrix[code] = {PROFILE_ID: _profile_disposition(code, None, profile_fields)}
    return matrix


def disposition_matrix_root(matrix: Mapping[str, Mapping[str, str]]) -> str:
    """Content-addressed root over the canonical disposition matrix (BOOT-01).

    A single leaf over the full matrix object: every (code, profile) cell is
    committed, so flipping any disposition (e.g. kind X from ``blocks`` to
    ``permits``) changes the root.
    """
    canonical = {code: dict(cells) for code, cells in matrix.items()}
    return leaf_hash(D_DISPOSITION_MATRIX, {"schema": D_DISPOSITION_MATRIX, "matrix": canonical})


def build_policy_bindings(
    *,
    diagnostic_registry_root: str,
    profile_manifest_root: str,
    disposition_matrix_root: str,
    source_policy_root: Optional[str],
    selection_profile_root: Optional[str],
) -> Dict[str, Any]:
    """Aggregate the certification fold's policy-input roots (BOOT-01, Pro §2).

    Every policy input the certification fold depends on is bound here by its
    content-addressed root. HONESTY (DUAL-01): a policy input with no real
    committed source object is bound as ``None`` (explicit absent marker), NOT a
    fabricated hash. The object's own root (:func:`leaf_hash` under
    ``D_POLICY_BINDINGS``) is committed into the certificate root set so a forged
    policy input flows through to ``certificate_root``.

    Bound (real committed objects): ``diagnostic_registry_root`` (the §3.5
    registry manifest), ``profile_manifest_root`` (the strict-profile manifest),
    ``disposition_matrix_root`` (the engine-derived disposition matrix),
    ``source_policy_root`` (the interpretation-policy manifest — the operative
    source/selection policy parameters this bundle was emitted under).

    Absent-with-reason: ``selection_profile_root`` — the engine has no reified
    selection-profile object DISTINCT from the interpretation policy (selection
    parameters live inside the interpretation-policy manifest already bound as
    ``source_policy_root``); binding a separate fabricated root would violate
    DUAL-01, so it is an explicit ``None``.
    """
    return {
        "schema": D_POLICY_BINDINGS,
        "diagnostic_registry_root": diagnostic_registry_root,
        "profile_manifest_root": profile_manifest_root,
        "disposition_matrix_root": disposition_matrix_root,
        "source_policy_root": source_policy_root,
        "selection_profile_root": selection_profile_root,
    }


def policy_bindings_root(bindings: Mapping[str, Any]) -> str:
    """Content-addressed root over the policy-bindings object (BOOT-01)."""
    return leaf_hash(D_POLICY_BINDINGS, bindings)


def build_diagnostic_registry_rows(profile_fields: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Emit §3.5 registry rows from the live engine observation registry.

    Registry "barrier" roles are strictness taxonomy metadata that can never
    appear on a runtime Finding; certificate spec §3.5 forbids registering
    codes whose role cannot produce a runtime finding, so they are excluded.

    BOOT-01 (item B): each row's ``profile_disposition`` is READ FROM the
    canonical engine-derived disposition matrix, never authored independently —
    a row cannot soften its own disposition out of step with the matrix.
    """
    matrix = build_disposition_matrix(profile_fields)
    rows: List[Dict[str, Any]] = []
    for code in sorted(FINDING_REGISTRY):
        spec = FINDING_REGISTRY[code]
        if spec.role == "barrier":
            continue
        is_fixed_term = spec.owner == "fixed_term_expiry"
        kind = _residual_kind_for_code(code, spec)
        allowed_kinds: List[str] = [kind] if spec.role in ("obligation", "violation") or is_fixed_term else []
        if code.startswith("uk_"):
            jurisdiction_scope = ["uk"]
        elif is_fixed_term:
            jurisdiction_scope = ["fi"]
        else:
            jurisdiction_scope = []
        rows.append(
            {
                "code": code,
                "canonical_semantic_code": code,
                "deprecated_aliases": list(_DEPRECATED_ALIASES.get(code, ())),
                "introduced_in": "lawvm.certificate.v0.4",
                "deprecated_in": None,
                "role": spec.role,
                "allowed_residual_kinds": allowed_kinds,
                "profile_disposition": dict(matrix[code]),
                "jurisdiction_scope": jurisdiction_scope,
                "doctrine_scope": ["fi.fixed_term_expiry.v1"] if is_fixed_term else [],
                "surface_language": "fi" if is_fixed_term else None,
                "surface_lexemes": (
                    ["voimassa"] if code == "TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED" else []
                ),
            }
        )
    # Bundle-local writer code (see SOURCE_ANCHOR_UNAVAILABLE_CODE SPEC-GAP).
    rows.append(
        {
            "code": SOURCE_ANCHOR_UNAVAILABLE_CODE,
            "canonical_semantic_code": SOURCE_ANCHOR_UNAVAILABLE_CODE,
            "deprecated_aliases": [],
            "introduced_in": "lawvm.certificate.v0.4",
            "deprecated_in": None,
            "role": "obligation",
            "allowed_residual_kinds": ["source_anchor_unavailable"],
            "profile_disposition": dict(matrix[SOURCE_ANCHOR_UNAVAILABLE_CODE]),
            "jurisdiction_scope": [],
            "doctrine_scope": [],
            "surface_language": None,
            "surface_lexemes": [],
        }
    )
    # Bundle-local cross-check code: diff-vs-receipt divergence is a
    # non-blocking observation (role=observation -> profile permits). It never
    # produces a residual; it only ever appears in the finding ledger when a
    # genuine divergence is detected, so a consistent replay leaves every
    # committed root unchanged.
    rows.append(
        {
            "code": RECEIPT_TRANSITION_DIVERGENCE_CODE,
            "canonical_semantic_code": RECEIPT_TRANSITION_DIVERGENCE_CODE,
            "deprecated_aliases": [],
            "introduced_in": "lawvm.certificate.v0.4",
            "deprecated_in": None,
            "role": "observation",
            "allowed_residual_kinds": [],
            "profile_disposition": dict(matrix[RECEIPT_TRANSITION_DIVERGENCE_CODE]),
            "jurisdiction_scope": [],
            "doctrine_scope": [],
            "surface_language": None,
            "surface_lexemes": [],
        }
    )
    rows.sort(key=lambda r: r["code"])
    return rows


# ---------------------------------------------------------------------------
# Receipt consistency cross-check (diff-vs-receipt divergence detector)
# ---------------------------------------------------------------------------

# Every landed WriteReceipt footprint TreePath begins at the addressable body
# root, whose segment renders as ``hcontainer:/`` under the receipt address
# grammar. The certificate's covering-state ``target_address`` is rooted at the
# same body but WITHOUT that prefix (covering_units addresses are body-relative).
# Stripping the prefix puts both grammars in one comparable space.
_RECEIPT_BODY_ROOT_PREFIX = "hcontainer:/"


def _receipt_footprint_addresses(receipt: WriteReceipt) -> set[str]:
    """Body-relative covering addresses a receipt declares as touched.

    Returns the receipt's ``declared_footprint`` rendered in the certificate's
    ``target_address`` grammar (leading ``hcontainer:/`` body-root prefix
    stripped). Footprints that do not start at the body root are returned
    verbatim — that itself is a shape the cross-check will report as
    unexplained rather than silently coerce.
    """
    out: set[str] = set()
    for path in receipt.declared_footprint:
        rendered = receipt_address_string(path)
        if rendered.startswith(_RECEIPT_BODY_ROOT_PREFIX):
            rendered = rendered[len(_RECEIPT_BODY_ROOT_PREFIX):]
        if rendered:
            out.add(rendered)
    return out


def _address_explains(footprint_addr: str, target_addr: str) -> bool:
    """True when a receipt footprint address explains a transition target.

    A covering-unit transition at ``target_addr`` is explained by a receipt
    footprint at ``footprint_addr`` when the two addresses are equal, or one is
    an ancestor of the other in the slash-separated address tree — the same
    covering relation ``_ops_for_covering`` uses to attribute ops to transitions
    (a whole-section write explains its subsection transitions; a subsection
    write explains the section transition it tiles).
    """
    if footprint_addr == target_addr:
        return True
    return target_addr.startswith(footprint_addr + "/") or footprint_addr.startswith(
        target_addr + "/"
    )


def cross_check_transitions_against_receipts(
    *,
    transition_rows: Sequence[Mapping[str, Any]],
    op_transitions: Mapping[str, Sequence[str]],
    write_receipts: Sequence[WriteReceipt],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Cross-check covering-state transitions against landed write receipts.

    The certificate derives its transitions from per-date covering-frontier
    state diffs; the apply boundary records per-op ``WriteReceipt`` footprints.
    These are two independent derivations of the same landed reality. This
    check asserts they agree WITHOUT replacing either: for every transition
    that attributes to one or more ops, at least one attributed op's receipt
    must declare a footprint that explains the transition's target address.

    Returns ``(divergences, notes)``:

    * ``divergences`` — one detail dict per genuinely divergent transition
      (attributed ops present, but no attributed receipt footprint explains the
      target address). The caller turns each into a NON-BLOCKING
      ``CERT.RECEIPT_TRANSITION_DIVERGENCE`` finding.
    * ``notes`` — human-readable accounting of the legitimate zero-receipt
      transitions (no attributed ops: temporary-act lapse restoration, the
      enacted-base first materialization). These are recorded, never flagged.

    A consistent replay yields ``([], [<zero-receipt accounting>])`` — no
    findings, so every committed certificate root is unchanged.
    """
    receipt_by_op: Dict[str, List[WriteReceipt]] = {}
    for receipt in write_receipts:
        receipt_by_op.setdefault(receipt.op_id, []).append(receipt)

    ops_by_transition: Dict[str, List[str]] = {}
    for op_id, transition_ids in op_transitions.items():
        for transition_id in transition_ids:
            ops_by_transition.setdefault(transition_id, []).append(op_id)

    divergences: List[Dict[str, Any]] = []
    zero_receipt_count = 0
    for row in transition_rows:
        transition_id = row["transition_id"]
        target_addr = row["target_address"]
        attributed_ops = ops_by_transition.get(transition_id, [])
        if not attributed_ops:
            # Legitimate zero-receipt transition: temporary-act lapse
            # restoration (flags.temporary_expiry) or the enacted-base first
            # materialization (flags.created with no attributing op). No
            # receipt is expected; record, never flag.
            zero_receipt_count += 1
            continue

        explaining_footprints: set[str] = set()
        attributed_with_receipts = 0
        for op_id in attributed_ops:
            for receipt in receipt_by_op.get(op_id, ()):
                attributed_with_receipts += 1
                for footprint_addr in _receipt_footprint_addresses(receipt):
                    if _address_explains(footprint_addr, target_addr):
                        explaining_footprints.add(footprint_addr)

        if not explaining_footprints:
            divergences.append(
                {
                    "transition_id": transition_id,
                    "target_address": target_addr,
                    "effective_date": row["effective_date"],
                    "attributed_op_ids": sorted(attributed_ops),
                    "attributed_ops_with_receipts": attributed_with_receipts,
                    "detail": (
                        "covering-state transition attributes to ops "
                        f"{sorted(attributed_ops)} but no attributed write "
                        f"receipt declares a footprint explaining target "
                        f"address {target_addr!r}"
                    ),
                }
            )

    notes = [
        f"receipt cross-check: {len(transition_rows)} transitions; "
        f"{zero_receipt_count} legitimate zero-receipt (no attributed op); "
        f"{len(divergences)} diff-vs-receipt divergence(s)"
    ]
    return divergences, notes


# ---------------------------------------------------------------------------
# Scope intersection (certificate spec §5.3) and status algebra (§5.2, §5.5)
# ---------------------------------------------------------------------------


def _address_overlaps(residual_address: Optional[str], row_address: Optional[str]) -> bool:
    """Address overlap: null scopes everything; otherwise prefix in either direction."""
    if residual_address is None or row_address is None:
        return True
    if residual_address == row_address:
        return True
    return row_address.startswith(residual_address + "/") or residual_address.startswith(row_address + "/")


def _date_ranges_overlap(
    a: Tuple[Optional[str], Optional[str]],
    b: Tuple[Optional[str], Optional[str]],
) -> bool:
    """Half-open ISO-date interval overlap; ``None`` end = unbounded (§5.3)."""
    a_start, a_end = a
    b_start, b_end = b
    if a_end is not None and b_start is not None and a_end <= b_start:
        return False
    if b_end is not None and a_start is not None and b_end <= a_start:
        return False
    return True


def residual_intersects_row(
    residual: Mapping[str, Any],
    *,
    row_address: str,
    row_interval: Tuple[str, Optional[str]],
) -> bool:
    scope = residual.get("scope") or {}
    date_range = scope.get("date_range") or [None, None]
    return _address_overlaps(scope.get("address"), row_address) and _date_ranges_overlap(
        (date_range[0], date_range[1]), row_interval
    )


def residual_effect(residual: Mapping[str, Any], profile_id: str) -> str:
    effect = (residual.get("profile_effect") or {}).get(profile_id)
    return effect if effect in ("blocks", "qualifies", "permits") else "permits"


_SEAM_TO_CERTIFICATION = {
    # §5.5 normative mapping for seam 0.2 statuses.
    "selected": "confirmed",
    "absent": "confirmed",
    "expired": "confirmed",  # confirmed NON-LIVE temporal state, never live text
    "expiry_unverified": "blocked",
    "address_not_found": "blocked",
    "ambiguous_address": "blocked",
    "invalid_address": "blocked",
    "ambiguous_missing_scope": "blocked",
    "unsupported_jurisdiction": "not_applicable",
}

# §5.5: the qualifying-residual override applies to live/absent assertions;
# expired stays "confirmed" (the spec text attaches the override to selected
# and absent only).
_QUALIFIABLE_SEAM_STATUSES = frozenset({"selected", "absent"})


def certification_status_for_row(
    seam_status: str,
    *,
    row_address: str,
    row_interval: Tuple[str, Optional[str]],
    residual_rows: Sequence[Mapping[str, Any]],
    profile_id: str = PROFILE_ID,
) -> str:
    base = _SEAM_TO_CERTIFICATION.get(seam_status)
    if base is None:
        raise BundleSpecError(f"seam status {seam_status!r} has no §5.5 certification mapping")
    if base == "confirmed" and seam_status in _QUALIFIABLE_SEAM_STATUSES:
        for residual in residual_rows:
            if residual_effect(residual, profile_id) == "qualifies" and residual_intersects_row(
                residual, row_address=row_address, row_interval=row_interval
            ):
                return "qualified"
    return base


def compute_certificate_status(
    *,
    residual_rows: Sequence[Mapping[str, Any]],
    certification_statuses: Sequence[str],
    registered_codes: frozenset[str],
    profile_id: str = PROFILE_ID,
    required_artifacts_present: bool = True,
    extra_blocking_residual_count: int = 0,
) -> str:
    """Certificate spec §5.2 status algebra — computed, never author-chosen.

    ``extra_blocking_residual_count`` (StageResult endgame Wave-5, ORCH-2): the
    number of BLOCKING per-stage residuals contributed by stages whose
    incompleteness must block a clean claim but whose residue does NOT fold into the
    flat ``residual_root`` (#5 broken refs, #10 dropped universe members). A non-zero
    count UNCONDITIONALLY forces ``blocked`` (reachable on the blocked FI corpus,
    genuine (C)) without perturbing the flat residual ledger (0-delta).
    """
    if not required_artifacts_present:
        return "blocked"
    if extra_blocking_residual_count > 0:
        return "blocked"
    for residual in residual_rows:
        code = residual.get("diagnostic_code") or ""
        if not code or code == "unclassified" or code not in registered_codes:
            return "blocked"
        if residual_effect(residual, profile_id) == "blocks":
            return "blocked"
    if any(status == "blocked" for status in certification_statuses):
        return "blocked"
    if any(status == "unknown" for status in certification_statuses):
        # unknown is INVALID inside a clean or qualified certificate (§5.5).
        return "blocked"
    if any(status == "qualified" for status in certification_statuses):
        return "qualified"
    return "clean"


# ---------------------------------------------------------------------------
# Bundle construction
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class BundleWriteResult:
    bundle_dir: str
    certificate_id: str
    build_id: str
    certificate_status: str
    statute_id: str
    title: str
    boundary_dates: List[str]
    transition_count: int
    seam_row_count: int
    residual_count: int
    finding_count: int
    roots: Dict[str, str]
    writer_notes: List[str]


def _artifact_id(engine_sid: str) -> str:
    year, num = engine_sid.split("/")
    return f"fi.finlex.alkup.{year}.{num}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _seam_spec_hash() -> str:
    spec_path = _repo_root() / "notes" / "SEAM_SPEC_PROVISION_STATE.md"
    if not spec_path.is_file():
        raise BundleSpecError(
            f"seam spec document not found at {spec_path}; cannot pin projection_spec_hash (§3.4)"
        )
    return _sha256_rendered(spec_path.read_bytes())


def _certified_core(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Trace spec §5.1 certified-core field set (hashed into transition_hash)."""
    return {
        "transition_id": row["transition_id"],
        "sequence": row["sequence"],
        "effective_date": row["effective_date"],
        "action": row["action"],
        "target_address": row["target_address"],
        "pre_hash": row["pre_hash"],
        "post_hash": row["post_hash"],
        "payload_hash": row["payload_hash"],
        "source_refs": row["source_refs"],
        "source_anchors": row["source_anchors"],
    }


def _finding_row(
    *,
    diagnostic_code: str,
    role: str,
    blocking: bool,
    address: Optional[str],
    date_range: List[Optional[str]],
    source_refs: List[str],
    phase: str,
    detail: Mapping[str, Any],
) -> Dict[str, Any]:
    row = {
        "diagnostic_code": diagnostic_code,
        "role": role,
        "blocking": blocking,
        "scope": {"address": address, "date_range": date_range},
        "source_refs": source_refs,
        "phase": phase,
        "detail": _plainify(detail, "finding.detail"),
    }
    row["finding_id"] = leaf_hash(D_FINDING_LEDGER + ".id", row)
    return row


def _residual_row(
    *,
    kind: str,
    diagnostic_code: str,
    role: str,
    blocking: bool,
    address: Optional[str],
    date_range: List[Optional[str]],
    source_text: str,
    rule_id: str,
    source_refs: List[str],
    finding_refs: List[str],
    profile_effect: Mapping[str, str],
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if kind not in _RESIDUAL_KINDS:
        raise BundleSpecError(f"residual kind {kind!r} outside the §5.4 vocabulary")
    row: Dict[str, Any] = {
        "kind": kind,
        "diagnostic_code": diagnostic_code,
        "role": role,
        "blocking": blocking,
        "scope": {"address": address, "date_range": date_range},
        "source_text": source_text,
        "rule_id": rule_id,
        "source_refs": source_refs,
        "finding_refs": finding_refs,
        "profile_effect": dict(profile_effect),
    }
    if extra:
        row.update(extra)
    row["residual_id"] = leaf_hash(D_RESIDUAL_LEDGER + ".id", row)
    return row


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(canonical_json_bytes(row).decode("ascii"))
            fh.write("\n")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )


def build_certificate_bundle(
    statute_id: str,
    out_dir: str | Path,
    *,
    granularity: str = DEFAULT_GRANULARITY,
    quiet: bool = True,
    graph_store_root: str | Path | None = None,
) -> BundleWriteResult:
    """Write an EXPERIMENTAL certificate bundle for one Finnish statute.

    ``statute_id`` accepts canonical 'num/year' (482/2024) or engine
    'year/num' (2024/482). The bundle is a local schema-pressure fixture —
    see the module docstring for the §11.3 boundary.

    Emission registers the bundle as a taint-checkable build in the
    provenance graph store (``graph_store_root``, defaulting to
    ``$LAWVM_GRAPH_STORE_ROOT`` then ``data/fi/v1/provenance_graph``): a
    build node keyed by ``cert:lawvm.certificate.v{spec}:{certificate_root}``
    plus one consumed_by_build edge per consumed ProvenanceAssertion (currently the
    writer consumes none, so the record carries
    ``consumption_instrumented=True, consumed_subject_count=0``).  If the
    recorder fails, the emission fails — bundle files already on disk are
    NOT considered published (no BundleWriteResult is returned).
    """
    if granularity not in ("subsection", "section"):
        raise BundleSpecError(
            f"granularity {granularity!r} outside the experimental-writer boundary "
            "(certificate spec §11.3: subsection or section)"
        )
    notes: List[str] = []
    canonical_id = _canonical_statute_id(statute_id)
    engine_id = _engine_statute_id(canonical_id)
    out_path = Path(out_dir)

    bundle = run_engine_replay(engine_id)

    # --- time axis: ALL timeline boundary dates (certificate spec §2.1) ---
    from lawvm.finland.fixed_term_expiry import extract_fixed_term_bounds

    extraction = extract_fixed_term_bounds(statute_id=canonical_id, timelines=bundle.timelines)
    boundary_dates = set(bundle.change_dates)
    for bound in extraction.bounds:
        # Work-level fixed-term expires_on dates are real state boundaries (§2.1).
        if bound.expires_on:
            boundary_dates.add(bound.expires_on)
    if not boundary_dates:
        raise BundleSpecError(
            f"statute {canonical_id} has no committed boundary dates; cannot declare a "
            "closed_interval time_scope (§1)"
        )
    boundary = sorted(boundary_dates)
    time_scope = {"kind": "closed_interval", "from": boundary[0], "to": boundary[-1]}

    # --- sources: bundle ALL bytes locally (§11.3 boundary) ---
    from lawvm.finland.corpus import _get_corpus_store

    corpus = _get_corpus_store()
    source_statutes: Dict[str, str] = {engine_id: "enacted_text"}
    for op in bundle.lo_ops:
        src = op.source
        if src is not None and src.statute_id and _engine_statute_id(src.statute_id) != engine_id:
            source_statutes[_engine_statute_id(src.statute_id)] = "amending_text"

    source_blobs: Dict[str, bytes] = {}  # artifact_id -> raw bytes
    source_identities: List[Dict[str, Any]] = []
    artifact_id_by_engine_sid: Dict[str, str] = {}
    for sid in sorted(source_statutes):
        role = source_statutes[sid]
        # Read through the content-witnessed surface (WAIST #1): the witness's
        # sha256 DigestWitness over the ACTUAL bytes becomes the source identity's
        # raw_source_hash committed into source_bundle_root — so the witness flows
        # from the read into the dossier root, derived from the read and never
        # reconstructed from sid. This un-severs read_source_witness /
        # read_amendment_witness in the certificate path.
        witnessed = (
            corpus.read_source_witness(sid)
            if role == "enacted_text"
            else corpus.read_amendment_witness(sid)
        )
        if witnessed is None:
            raise BundleSpecError(
                f"source bytes for {sid} unavailable in local corpus; the experimental "
                "writer MUST bundle all source bytes (§11.3) — no URL-only references"
            )
        data, source_witness = witnessed
        if source_witness.digest is None:
            raise BundleSpecError(
                f"source witness for {sid} carries no content digest; the source "
                "identity must commit a content-addressed hash"
            )
        raw_hash = f"{source_witness.digest.digest_algorithm}:{source_witness.digest.digest}"
        aid = _artifact_id(sid)
        artifact_id_by_engine_sid[sid] = aid
        source_blobs[aid] = data
        year, num = sid.split("/")
        source_identities.append(
            {
                # §3.2 SourceArtifact identity object — identity metadata plus
                # the raw byte hash, never the byte hash alone.
                "source_artifact_id": aid,
                "jurisdiction": "fi",
                "work_kind": "normative_act",
                "source_role": role,
                "canonical_id": f"{num}/{year}",
                "locator": f"sources/{raw_hash.removeprefix('sha256:')}.bin",
                "raw_source_hash": raw_hash,
            }
        )
    source_identities.sort(key=lambda r: r["source_artifact_id"])
    source_leaves = [leaf_hash(D_SOURCE_ARTIFACT, identity) for identity in source_identities]
    source_bundle_root = set_root(D_SOURCE_BUNDLE, source_leaves)
    base_artifact_id = artifact_id_by_engine_sid[engine_id]

    # --- per-stage account subroots (StageResult endgame WAIST #9) ---
    # The FI token/source-unit stage is the first LIVE feeder: its
    # `StageResult[SourceSurfaceBundle]` coverage/residuals (the segmentation
    # partition) flow into the dossier as a per-stage account. Built from the
    # SAME enacted body bytes already bundled above (0-delta: no new read). Each
    # later StageResult-carried waist appends its row here.
    from lawvm.finland.legal_surface.bundle import build_surface_bundle_staged

    stage_account_rows: List[Dict[str, Any]] = []
    surface_stage = build_surface_bundle_staged(
        source_blobs[base_artifact_id], canonical_id
    )
    stage_account_rows.append(
        build_stage_account_row("fi.legal_surface.source_unit", surface_stage)
    )

    # The FI timeline/materialization stage (StageResult endgame WAIST #8): the
    # PIT materialization coverage account that the plain `materialize_pit` path
    # used to DISCARD. The FI replay carries it on `ReplayProducts` as a typed
    # `StageResult[IRStatute]`; we route it into the dossier as a per-stage
    # account here, and `verify_bundle` BRANCHES on its `coverage.violation` so a
    # degraded/violating materialization makes a CLEAN certificate impossible.
    materialization_stage = _fi_materialization_stage(bundle, boundary[-1])
    stage_account_rows.append(
        build_stage_account_row(STAGE_TIMELINE_MATERIALIZATION, materialization_stage)
    )
    # The cert claims totality over a fully-materialized work; a materialization
    # whose coverage carries an unowned-signal violation cannot be silently
    # certified clean (the §LEDGER "incompleteness blocks a clean claim").
    _require_clean_materialization_stage(materialization_stage)

    # --- StageResult endgame Wave-5: route the remaining 6 per-stage
    # accounts so EVERY pipeline stage emits a checkable certificate root (the (D)
    # deliverable). All additive read-offs of accounts the value path already
    # produces; each lands in the UNCONDITIONAL `_verify_stage_accounts` recompute
    # (checkable on the BLOCKED 482/2024). 0-delta on the flat roots — only
    # `stage_accounts_root` gains members (the #2/#8 precedent).

    # #1 source identity — coverage over the witnessed reads already in hand.
    source_identity_stage = _fi_source_identity_stage(source_identities)
    stage_account_rows.append(
        build_stage_account_row(STAGE_SOURCE_IDENTITY, source_identity_stage)
    )

    # #3 structure write-footprint (SEAM B) — the type-carried per-op structural
    # account aggregated on ReplayProducts (apply already owns the divergence
    # Finding verdict; this is the additive checkable attribution).
    structural_stage = bundle.result.products.structural_stage
    if structural_stage is None:
        raise BundleSpecError(
            "FI replay carried no structural write-footprint StageResult "
            f"(ReplayProducts.structural_stage) for {engine_id}; the #3 account "
            "cannot be routed into the dossier"
        )
    stage_account_rows.append(
        build_stage_account_row(STAGE_STRUCTURE_WRITE_FOOTPRINT, structural_stage)
    )

    # #4 source-syntax forest (SEAM A) — the token-partition forest account over
    # the #2 bundle units (the same forest `_gate_forest_coverage` builds).
    source_syntax_stage = _fi_source_syntax_stage(surface_stage.value)
    stage_account_rows.append(
        build_stage_account_row(STAGE_SOURCE_SYNTAX_FOREST, source_syntax_stage)
    )

    # #5 legal-surface graph (SEAM A) — the surface-node partition from the enacted
    # body bytes. Its BLOCKING residual (a broken ref) contributes to the cert
    # status below (PART 2 status-contribution, reachable — NOT a clean-gated
    # firewall).
    surface_graph_stage = _fi_legal_surface_graph_stage(
        source_blobs[base_artifact_id], engine_id
    )
    stage_account_rows.append(
        build_stage_account_row(STAGE_LEGAL_SURFACE_GRAPH, surface_graph_stage)
    )

    # #6 canonical-op compile (SEAM B) — the per-amendment compile partition
    # aggregated on ReplayProducts (the decline VERDICT rides the existing #6
    # residual/finding channel; this is the additive checkable account).
    canonical_op_stage = bundle.result.products.canonical_op_stage
    if canonical_op_stage is None:
        raise BundleSpecError(
            "FI replay carried no canonical-op compile StageResult "
            f"(ReplayProducts.canonical_op_stage) for {engine_id}; the #6 account "
            "cannot be routed into the dossier"
        )
    stage_account_rows.append(
        build_stage_account_row(STAGE_CANONICAL_OP_COMPILE, canonical_op_stage)
    )

    # #10 projection interlinks + overlays (SEAM A, ORCH-5: both). A
    # silently-dropped universe member rides as a BLOCKING projection_residual; that
    # residual contributes to the cert status below (PART 2).
    from lawvm.tools.export_fi_interlinks import (
        _project_interlinks_for_statute,
        _project_overlays_for_statute,
    )

    projection_interlinks_stage = _fi_projection_stage(
        _project_interlinks_for_statute, engine_id, corpus
    )
    stage_account_rows.append(
        build_stage_account_row(STAGE_PROJECTION_INTERLINKS, projection_interlinks_stage)
    )
    projection_overlays_stage = _fi_projection_stage(
        _project_overlays_for_statute, engine_id, corpus
    )
    stage_account_rows.append(
        build_stage_account_row(STAGE_PROJECTION_OVERLAYS, projection_overlays_stage)
    )

    # PART 2 (ORCH-2): the #5/#10 BLOCKING residuals contribute to the residue
    # `compute_certificate_status` reads so a broken-ref / dropped-universe-member
    # UNCONDITIONALLY forces blocked (genuine (C), reachable). 0 on the green corpus.
    status_contributing_stages: List[Tuple[str, "StageResult[Any]"]] = [
        (STAGE_LEGAL_SURFACE_GRAPH, surface_graph_stage),
        (STAGE_PROJECTION_INTERLINKS, projection_interlinks_stage),
        (STAGE_PROJECTION_OVERLAYS, projection_overlays_stage),
    ]
    extra_blocking_residual_count = _stage_blocking_residual_count(
        status_contributing_stages
    )

    # EV-03 (residual-ledger monotonicity): assert no residual COUNTED in a stage's
    # coverage violation class silently vanished from that stage's committed residual
    # ledger (the §0 conservation law over the per-stage account fold). The existing
    # `_verify_stage_accounts` recompute proves the rows hash to their subroots; this
    # is the orthogonal conservation check the arithmetic/hash checks do NOT cover.
    # On the green corpus every stage carries violation==0 → silent (0-delta). A real
    # non-monotone account is a producer defect, never a corpus fact, so it fails
    # loud here rather than silently certifying a dropped-residual ledger.
    _require_monotone_stage_residual_ledger(stage_account_rows)

    # EV-07 (self-evidencing diagnostic totality): every source-text-failure residual
    # the per-stage accounts committed (unowned_violation / typed_residual) MUST embed
    # its verbatim offending snippet — never an opaque, snippet-less diagnostic about
    # unhandled source text. On the green corpus the forest/surface producers set the
    # text by construction → silent (0-delta).
    _require_self_evidencing_stage_residuals(stage_account_rows)

    stage_accounts_root_value = stage_accounts_root(stage_account_rows)

    # --- apply/replay execution-authority firewall (StageResult endgame WAIST
    # #7) --- The per-replay apply authority (AND over every landed write):
    # re-derived descriptively from the landed receipts + findings this dossier
    # already consumes. Routed into the dossier as an additive subroot (0-delta:
    # an authorized replay yields a value-stable single leaf that never folds into
    # the flat roots). The firewall bite (the clean-claim gate) lives just below,
    # after `certificate_status` is computed.
    apply_authority = _fi_apply_authority(bundle)
    apply_authority_row_value = apply_authority_row(apply_authority)
    apply_authority_root_value = apply_authority_root(apply_authority)

    # --- trace: covering-state evolution per boundary date (§10 carve-out) ---
    ops_by_date = _index_ops_by_date(bundle.lo_ops)
    expiry_ops_by_date = _index_ops_by_expiry_date(bundle.lo_ops)

    prev_state: Dict[str, str] = {}
    states_by_date: Dict[str, Dict[str, str]] = {}
    blobs: Dict[str, Dict[str, Any]] = {}  # bare-hex structural hash -> §2.1 node json
    transition_rows: List[Dict[str, Any]] = []
    checkpoint_rows: List[Dict[str, Any]] = []
    op_transitions: Dict[str, List[str]] = {}
    seq = 0
    for date in boundary:
        tree = materialize_oracle_tree(bundle, date)
        units = covering_units(tree, "", granularity)
        cur_state: Dict[str, str] = {}
        cur_order: List[str] = []
        for addr, node in units:
            h = structural_subtree_hash(node)
            cur_state[addr] = h
            cur_order.append(addr)
            if h not in blobs:
                blobs[h] = node.to_jsonable_dict()
        states_by_date[date] = dict(cur_state)

        # §8.1 covering-state checkpoint hash (frozen byte recipe, reused from
        # the engine via reproducible_tree_hash's exact algorithm below in
        # verify; here computed through the same engine primitive).
        from lawvm.tools.export_transition_graph import reproducible_tree_hash

        tree_hash = reproducible_tree_hash(list(cur_state.items()))
        checkpoint_rows.append(
            {
                "date": date,
                "address_prefix": "",
                "tree_hash": "sha256:" + tree_hash,
                "active_unit_count": len(cur_state),
            }
        )

        ops_on_date = ops_by_date.get(date, [])
        expiring_on_date = expiry_ops_by_date.get(date, [])
        all_addrs = list(dict.fromkeys(list(prev_state.keys()) + cur_order))
        for addr in all_addrs:
            pre = prev_state.get(addr, "")
            post = cur_state.get(addr, "")
            if pre == post:
                continue
            seq += 1
            action = "delete_subtree" if post == "" else "set_subtree"
            transition_id = f"t{seq:06d}:{date}:{addr}"

            ops = _ops_for_covering(ops_on_date, addr)
            expiring = _ops_for_covering(expiring_on_date, addr)
            ref_sids: set[str] = set()
            for op in ops + expiring:
                src = op.source
                if src is not None and src.statute_id:
                    ref_sids.add(_engine_statute_id(src.statute_id))
            if pre == "":
                # First materialization carries the enacted base text; the
                # base statute is a driving instrument of the observed state.
                ref_sids.add(engine_id)
            source_refs = sorted(
                artifact_id_by_engine_sid[sid] for sid in ref_sids if sid in artifact_id_by_engine_sid
            )
            dropped = sorted(sid for sid in ref_sids if sid not in artifact_id_by_engine_sid)
            if dropped:
                raise BundleSpecError(
                    f"transition {transition_id} driven by unbundled source(s) {dropped}; "
                    "all source bytes must be bundled (§11.3)"
                )

            kind_set = {str(o.action) for o in ops}
            summaries = [_legal_op_summary(o) for o in ops[:3]]
            if expiring:
                kind_set.add("expiry")
                summaries.extend(f"expiry of {_legal_op_summary(o)}" for o in expiring[:3])
            flags: Dict[str, Any] = {}
            if post == "":
                flags["removed"] = True
            if pre == "" and post != "":
                flags["created"] = True
            if expiring and not ops:
                flags["temporary_expiry"] = True

            row = {
                # certified core (trace spec §5.1)
                "transition_id": transition_id,
                "sequence": seq,
                "effective_date": date,
                "action": action,
                "target_address": addr,
                "pre_hash": ("sha256:" + pre) if pre else "",
                "post_hash": ("sha256:" + post) if post else "",
                "payload_hash": ("sha256:" + post) if post else "",
                "source_refs": source_refs,
                # Experimental writer: state-diff-derived transitions carry no
                # byte anchors; every source_ref gets a
                # kind=source_anchor_unavailable residual (trace spec §7).
                "source_anchors": [],
                # display annotation (NOT hashed)
                "legal_op_kind": ",".join(sorted(kind_set)),
                "legal_op_summary": " | ".join(summaries[:4]),
                "preparatory_refs": [],
                "expires_date": "",
                "flags": flags,
            }
            transition_rows.append(row)
            for op in ops + expiring:
                op_transitions.setdefault(op.op_id, []).append(transition_id)
        prev_state = cur_state

    # --- byte-level source anchoring (trace spec §5.1/§7) ---
    # Promote genuine source anchors carried by landed receipts onto the
    # transition rows that the receipts drove. The anchor is RE-VERIFIED against
    # the bundle's own source bytes before it is certified: a certified anchor
    # must satisfy source_blobs[aid][off:off+len] == quote bytes AND match the
    # recorded quote_hash. Any anchor that fails this independent re-derivation
    # (e.g. the receipt anchored a corrigendum-corrected byte stream that
    # differs from the bundled raw source) is dropped — the transition then
    # keeps its fail-loud SOURCE_ANCHOR_UNAVAILABLE residual. Anchors are never
    # fabricated and never trusted blindly. ``anchored_refs`` records the
    # (transition_id, source_ref) pairs that earned a certified anchor so the
    # residual loop can suppress only those.
    #
    # The join is on the SOURCE ARTIFACT, not on op_id: a receipt's anchor names
    # the amendment clause (source_artifact_id) whose verbatim source bytes drove
    # the write. Receipt op_ids and the engine's lo_op op_ids live in different
    # id spaces (the receipt boundary mints its own), so an op_id join would
    # never fire. Anchoring a transition to its driving source artifact is the
    # sound relation: the certified anchor is that artifact's clause provenance,
    # and the transition declares that artifact in ``source_refs``.
    def _verified_anchor_json(anchor: "SourceAnchor", artifact_id: str) -> Optional[Dict[str, Any]]:
        blob = source_blobs.get(artifact_id)
        if blob is None:
            return None
        end = anchor.byte_offset + anchor.byte_len
        if end > len(blob):
            return None
        quoted = blob[anchor.byte_offset:end]
        if "sha256:" + hashlib.sha256(quoted).hexdigest() != anchor.quote_hash:
            return None
        return anchor.as_jsonable()

    # One verified anchor per source artifact, keyed by bundle artifact_id.
    verified_anchor_by_artifact: Dict[str, Dict[str, Any]] = {}
    for _receipt in bundle.result.write_receipts:
        anchor = _receipt.source_anchor
        if anchor is None:
            continue
        artifact_id = artifact_id_by_engine_sid.get(
            _engine_statute_id(anchor.source_artifact_id)
        )
        if artifact_id is None or artifact_id in verified_anchor_by_artifact:
            continue
        verified = _verified_anchor_json(anchor, artifact_id)
        if verified is None:
            continue
        verified_anchor_by_artifact[artifact_id] = verified

    anchored_refs: set[Tuple[str, str]] = set()
    for row in transition_rows:
        transition_id = row["transition_id"]
        anchors_for_row: Dict[str, Dict[str, Any]] = {}
        for ref in row["source_refs"]:
            verified = verified_anchor_by_artifact.get(ref)
            if verified is None:
                continue
            anchors_for_row[ref] = verified
            anchored_refs.add((transition_id, ref))
        if anchors_for_row:
            row["source_anchors"] = [anchors_for_row[aid] for aid in sorted(anchors_for_row)]

    transition_leaves = [leaf_hash(D_TRANSITION, _certified_core(r)) for r in transition_rows]
    certified_tree_transition_root = list_root(D_TRACE, transition_leaves)

    blob_rows = [
        {"content_hash": "sha256:" + h, "content_json": blobs[h]} for h in sorted(blobs)
    ]
    content_blobs_root = set_root(
        D_CONTENT_BLOBS, [leaf_hash(D_CONTENT_BLOB, row) for row in blob_rows]
    )

    base_tree = {
        # Trace spec §3: the Finland exporter family starts from an EMPTY
        # covering state; the first change date's transitions establish it.
        "schema": D_BASE_TREE,
        "work_id": f"fi:act:{canonical_id}",
        "jurisdiction": "fi",
        "slice_prefix": "",
        "granularity": granularity,
        "units": [],
    }
    base_tree_root = leaf_hash(D_BASE_TREE, base_tree)
    materialization_root = list_root(
        D_MATERIALIZATION, [leaf_hash(D_STATE_ROOT, row) for row in checkpoint_rows]
    )
    change_dates_root = set_root(D_CHANGE_DATES, boundary)

    # --- policy manifests (§3.5) ---
    from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
    from lawvm.finland.strict_profile import default_finland_strict_profile

    engine_profile = default_finland_strict_profile()
    profile_fields = {
        f.name: getattr(engine_profile, f.name) for f in dataclasses.fields(engine_profile)
    }
    profile_manifest = {
        "schema": D_STRICT_PROFILE,
        "profile_id": PROFILE_ID,
        "engine_profile": profile_fields,
        "engine_profile_fingerprint": "sha256:" + compute_strict_profile_fingerprint(engine_profile),
    }
    profile_hash = leaf_hash(D_STRICT_PROFILE, profile_manifest)

    # SPEC-NOTE §3.5: the engine has no reified interpretation-policy object
    # for lawvm.fi.default.v1 (only an unused fingerprint hook) — an explicit
    # checker-v0 non-goal. The manifest pins the interpretation parameters
    # this bundle was emitted under (the §3.5 policy-manifest minimum).
    policy_manifest = {
        "schema": D_INTERPRETATION_POLICY,
        "policy_id": POLICY_ID,
        "parameters": {
            "jurisdiction": "fi",
            "query_type": "governing",
            "granularity": granularity,
            "synthesize_repeal_placeholders": True,
            "fixed_term_statute_bounds": "default_on",
            "selection": "overlay_rail_over_background; latest (effective, enacted) within rail",
        },
    }
    policy_hash = leaf_hash(D_INTERPRETATION_POLICY, policy_manifest)

    seam_spec_hash = _seam_spec_hash()
    projection_specs_manifest = {
        "schema": D_PROJECTION_SPECS,
        "projections": {
            "seam": {
                "schema": SEAM_SCHEMA,
                "spec_version": SEAM_SPEC_VERSION,
                "spec_hash": seam_spec_hash,
                # §3.4/§3.5: pinned run-provenance exclusion list for the
                # projection-hash input.
                "hash_excluded_members": list(SEAM_HASH_EXCLUDED_MEMBERS),
            }
        },
    }
    projection_specs_hash = leaf_hash(D_PROJECTION_SPECS, projection_specs_manifest)

    # BOOT-01: the canonical engine-derived disposition matrix is the single
    # authority for blocks/qualifies/permits. The registry rows AND the residual
    # profile_effect copies both derive from it (item B); the residual stamp uses
    # `disposition_by_code` which reads the MATRIX, not the registry rows.
    disposition_matrix = build_disposition_matrix(profile_fields)
    disposition_matrix_hash = disposition_matrix_root(disposition_matrix)
    registry_rows = build_diagnostic_registry_rows(profile_fields)
    diagnostic_registry_manifest = {"schema": D_DIAGNOSTIC_REGISTRY, "rows": registry_rows}
    diagnostic_registry_hash = leaf_hash(D_DIAGNOSTIC_REGISTRY, diagnostic_registry_manifest)
    registered_codes = frozenset(r["code"] for r in registry_rows)
    disposition_by_code = {code: cells[PROFILE_ID] for code, cells in disposition_matrix.items()}

    # BOOT-01 (Pro §2/§8 item A): bind every policy input the certification fold
    # depends on by content root into one policy-bindings object, whose root is
    # committed in the certificate root set. A forged registry/profile/disposition
    # matrix changes a bound root -> policy_bindings_root -> certificate_root.
    policy_bindings_manifest = build_policy_bindings(
        diagnostic_registry_root=diagnostic_registry_hash,
        profile_manifest_root=profile_hash,
        disposition_matrix_root=disposition_matrix_hash,
        # The interpretation-policy manifest is the operative source/selection
        # policy this bundle was emitted under (real committed object).
        source_policy_root=policy_hash,
        # Absent-with-reason (DUAL-01): no reified selection-profile object
        # distinct from the interpretation policy — see build_policy_bindings.
        selection_profile_root=None,
    )
    policy_bindings_hash = policy_bindings_root(policy_bindings_manifest)

    checker_contract = {"checker_version": CHECKER_VERSION, "hash_profile": HASH_PROFILE}
    checker_contract_manifest = {"schema": D_CHECKER_CONTRACT, **checker_contract}
    checker_contract_hash = leaf_hash(D_CHECKER_CONTRACT, checker_contract_manifest)

    # --- findings ledger (§5.7) ---
    finding_rows: List[Dict[str, Any]] = []
    seen_finding_ids: set[str] = set()
    full_range: List[Optional[str]] = [time_scope["from"], time_scope["to"]]

    def _add_finding(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if row["finding_id"] in seen_finding_ids:
            # Identical rows collapse under set semantics (§3.1.1 forbids
            # duplicate leaves).
            return None
        seen_finding_ids.add(row["finding_id"])
        finding_rows.append(row)
        return row

    finding_for_residual: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for finding in bundle.result.findings:
        if finding.kind not in registered_codes:
            raise BundleSpecError(
                f"replay finding kind {finding.kind!r} not in the pinned diagnostic registry"
            )
        src_refs = []
        if finding.source_statute:
            sid = _engine_statute_id(finding.source_statute)
            if sid in artifact_id_by_engine_sid:
                src_refs = [artifact_id_by_engine_sid[sid]]
        row = _finding_row(
            diagnostic_code=finding.kind,
            role=finding.role,
            blocking=bool(finding.blocking),
            address=None,
            date_range=list(full_range),
            source_refs=src_refs,
            phase=finding.stage,
            detail=finding.detail,
        )
        added = _add_finding(row)
        if added is not None and finding.blocking:
            finding_for_residual.append((added, dict(finding.detail)))

    for diagnostic in extraction.diagnostics:
        spec = FINDING_REGISTRY.get(diagnostic.code)
        if spec is None:
            raise BundleSpecError(
                f"fixed-term diagnostic code {diagnostic.code!r} not in the engine registry"
            )
        blocking = spec.role == "obligation" and spec.default_enforcement in ("hard_fail", "strict_fail")
        row = _finding_row(
            diagnostic_code=diagnostic.code,
            role="obligation" if spec.role == "obligation" else "observation",
            blocking=blocking,
            address=diagnostic.address or None,
            date_range=[diagnostic.effective or time_scope["from"], None],
            source_refs=[base_artifact_id],
            phase="fixed_term_expiry",
            detail={"detail": diagnostic.detail, "clause_text": diagnostic.clause_text},
        )
        added = _add_finding(row)
        if added is not None and blocking:
            finding_for_residual.append(
                (added, {"source_text": diagnostic.clause_text, "message": diagnostic.detail})
            )

    # --- receipt consistency cross-check (diff-vs-receipt divergence) ---
    # Assert the certificate's covering-state transitions agree with the
    # landed WriteReceipts carried up on the ReplayResult. Divergences become
    # NON-BLOCKING observations; legitimate zero-receipt transitions are noted,
    # never flagged, so a consistent replay leaves every committed root
    # unchanged.
    receipt_divergences, receipt_notes = cross_check_transitions_against_receipts(
        transition_rows=transition_rows,
        op_transitions=op_transitions,
        write_receipts=bundle.result.write_receipts,
    )
    notes.extend(receipt_notes)
    for divergence in receipt_divergences:
        _add_finding(
            _finding_row(
                diagnostic_code=RECEIPT_TRANSITION_DIVERGENCE_CODE,
                role="observation",
                blocking=False,
                address=divergence["target_address"],
                date_range=[divergence["effective_date"], None],
                source_refs=[],
                phase="receipt_cross_check",
                detail=divergence,
            )
        )

    # --- residual ledger (§5.4, §5.6) ---
    residual_rows: List[Dict[str, Any]] = []
    seen_residual_ids: set[str] = set()

    def _add_residual(row: Dict[str, Any]) -> None:
        if row["residual_id"] in seen_residual_ids:
            return
        seen_residual_ids.add(row["residual_id"])
        residual_rows.append(row)

    for finding_row, detail in finding_for_residual:
        code = finding_row["diagnostic_code"]
        derived = disposition_by_code.get(code)
        if derived != "blocks":
            raise BundleSpecError(
                f"blocking finding {code!r} maps to registry disposition {derived!r}; an "
                "emitter must never soften a blocking finding (§5.4)"
            )
        kind = _residual_kind_for_code(code, FINDING_REGISTRY.get(code))
        source_text = str(
            detail.get("source_text") or detail.get("clause_text") or detail.get("message") or ""
        )
        if kind == "expiry_unverified" and not source_text:
            raise BundleSpecError(
                f"expiry_unverified residual for {code!r} lacks self-evidencing source_text (§5.4)"
            )
        _add_residual(
            _residual_row(
                kind=kind,
                diagnostic_code=code,
                role=finding_row["role"],
                blocking=True,
                address=finding_row["scope"]["address"],
                date_range=list(finding_row["scope"]["date_range"]),
                source_text=source_text,
                # §5.4: rule_id required where the producing surface carries
                # one; "" has fixed semantics — no grammar-family attribution
                # exists (FixedTermDiagnostic fails before family selection).
                rule_id=str(detail.get("rule_id") or ""),
                source_refs=list(finding_row["source_refs"]),
                finding_refs=[finding_row["finding_id"]],
                profile_effect={PROFILE_ID: "blocks"},
            )
        )

    # Trace spec §7: every source_ref WITHOUT a certified byte anchor needs a
    # kind=source_anchor_unavailable residual naming the transition and ref.
    # When a (transition, ref) pair earned a re-verified byte anchor above, it
    # is omitted from the unavailable ledger — the certified anchor on the
    # transition row's certified core IS its provenance. The fail-loud residual
    # remains for every ref that genuinely could not be anchored.
    for row in transition_rows:
        for ref in row["source_refs"]:
            if (row["transition_id"], ref) in anchored_refs:
                continue
            _add_residual(
                _residual_row(
                    kind="source_anchor_unavailable",
                    diagnostic_code=SOURCE_ANCHOR_UNAVAILABLE_CODE,
                    role="obligation",
                    blocking=False,
                    address=row["target_address"],
                    date_range=[row["effective_date"], None],
                    source_text="",
                    rule_id="",
                    source_refs=[ref],
                    finding_refs=[],
                    profile_effect={PROFILE_ID: disposition_by_code[SOURCE_ANCHOR_UNAVAILABLE_CODE]},
                    extra={"transition_id": row["transition_id"]},
                )
            )

    residual_root = set_root(
        D_RESIDUAL_LEDGER, [leaf_hash(D_RESIDUAL_LEDGER, row) for row in residual_rows]
    )
    finding_root = set_root(
        D_FINDING_LEDGER, [leaf_hash(D_FINDING_LEDGER, row) for row in finding_rows]
    )

    # --- seam projection rows (§3.4, §5.5) ---
    from lawvm.tools.provision_state import build_provision_state_response

    migration_events = tuple(bundle.result.products.migration_events)
    intervals: List[Tuple[str, Optional[str]]] = [
        (boundary[i], boundary[i + 1] if i + 1 < len(boundary) else None)
        for i in range(len(boundary))
    ]
    seam_entries: List[Dict[str, Any]] = []  # wrapper rows, parentage filled later
    projection_hashes: List[str] = []
    blocked_row_count = 0
    qualified_row_count = 0
    certification_statuses: List[str] = []
    for start, end in intervals:
        for addr in sorted(states_by_date[start]):
            payload = build_provision_state_response(
                timelines=bundle.timelines,
                migration_events=migration_events,
                statute_id=canonical_id,
                jurisdiction="fi",
                provision=addr,
                as_of=start,
                query_type="governing",
                territory=None,
                title=bundle.title,
            )
            # §3.4: only the payload's hash view is hashed (run-provenance
            # excluded); parentage is a wrapper member.
            projection_hash = projection_payload_hash(payload, SEAM_HASH_EXCLUDED_MEMBERS)
            projection_hashes.append(projection_hash)
            certification_status = certification_status_for_row(
                payload["provision_status"],
                row_address=addr,
                row_interval=(start, end),
                residual_rows=residual_rows,
            )
            certification_statuses.append(certification_status)
            if certification_status == "blocked":
                blocked_row_count += 1
            if certification_status == "qualified":
                qualified_row_count += 1
            seam_entries.append(
                {
                    "projection_payload": payload,
                    "certification_status": certification_status,
                    "universe": {"address": addr, "interval": [start, end]},
                    "_projection_hash": projection_hash,
                }
            )
    seam_projection_root = set_root(D_PROJECTION_SEAM, projection_hashes)
    projection_root_preimage = {
        "seam": seam_projection_root,
        "dump": None,
        "transition_graph": None,
    }
    projection_root = leaf_hash(D_PROJECTION_ROOT, projection_root_preimage)

    # --- coverage artifacts (§4.1, §5.7; declared-coverage-only boundary) ---
    source_unit_rows: List[Dict[str, Any]] = []
    for identity in source_identities:
        aid = identity["source_artifact_id"]
        data = source_blobs[aid]
        if not any(aid in r["source_refs"] for r in transition_rows):
            raise BundleSpecError(
                f"coverage row for {aid} would claim compiled with no transition source-ref (§5.7)"
            )
        source_unit_rows.append(
            {
                # Document-granularity declared coverage: the writer enumerates
                # whole source artifacts, not intra-document units (§4.1 makes
                # declared coverage a committed claim, not a completeness one).
                "source_unit_id": f"{aid}:document",
                "source_anchor": {
                    "source_artifact_id": aid,
                    "locator": identity["locator"],
                    "span_unit": "byte",
                    "span": [0, len(data)],
                    "quote_hash": _sha256_rendered(data),
                },
                "classification": "operative",
                "source_unit_status": "compiled",
                "refs": [],
            }
        )
    potential_op_rows: List[Dict[str, Any]] = []
    for op in bundle.lo_ops:
        src = op.source
        sid = _engine_statute_id(src.statute_id) if src is not None and src.statute_id else engine_id
        aid = artifact_id_by_engine_sid.get(sid, base_artifact_id)
        data = source_blobs[aid]
        refs = sorted(set(op_transitions.get(op.op_id, [])))
        if not refs:
            notes.append(
                f"L2 op {op.op_id!r} produced no covering-state diff; declared as 'suppressed' "
                "in potential_operation_coverage"
            )
        potential_op_rows.append(
            {
                "potential_operation_id": op.op_id,
                "source_anchor": {
                    "source_artifact_id": aid,
                    "locator": next(
                        i["locator"] for i in source_identities if i["source_artifact_id"] == aid
                    ),
                    "span_unit": "byte",
                    "span": [0, len(data)],
                    "quote_hash": _sha256_rendered(data),
                },
                "classification": "compiled" if refs else "suppressed",
                "refs": refs,
                "action": str(op.action),
                "target": str(op.target) if op.target is not None else "",
            }
        )
    source_unit_coverage_root = set_root(
        D_SOURCE_UNIT_COVERAGE, [leaf_hash(D_SOURCE_UNIT_COVERAGE, r) for r in source_unit_rows]
    )
    potential_op_coverage_root = set_root(
        D_POTENTIAL_OP_COVERAGE, [leaf_hash(D_POTENTIAL_OP_COVERAGE, r) for r in potential_op_rows]
    )
    coverage_root = leaf_hash(
        D_COVERAGE,
        {
            "source_unit_coverage": source_unit_coverage_root,
            "potential_operation_coverage": potential_op_coverage_root,
        },
    )

    # --- residual summary + certificate status (§5.1, §5.2 — computed) ---
    by_kind: Dict[str, int] = {}
    blocking_count = qualified_count = observation_count = frontier_count = 0
    for row in residual_rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        effect = residual_effect(row, PROFILE_ID)
        if effect == "blocks":
            blocking_count += 1
        elif effect == "qualifies":
            qualified_count += 1
        else:
            observation_count += 1
        if row["kind"] == "manual_frontier":
            frontier_count += 1
    residual_summary = {
        "blocking_count": blocking_count,
        "qualified_count": qualified_count,
        "observation_count": observation_count,
        "frontier_count": frontier_count,
        "by_kind": dict(sorted(by_kind.items())),
    }
    certificate_status = compute_certificate_status(
        residual_rows=residual_rows,
        certification_statuses=certification_statuses,
        registered_codes=registered_codes,
        extra_blocking_residual_count=extra_blocking_residual_count,
    )

    # WAIST #7 firewall bite: a CLEAN/authoritative dossier requires the
    # per-replay apply authority to be replay_authorized. An unauthorized replay
    # (neutral/un-granted ExecutionAuthorization, or a write outside the
    # conservative apply gate) cannot produce an authoritative receipt — the clean
    # claim is forbidden here (it may still emit a BLOCKED dossier). On the green
    # corpus every replay authorizes, so this never fires (0-delta). RED if the
    # authority is severed to neutral or the gate is wired to ignore it.
    if certificate_status == "clean":
        _require_authorized_replay(apply_authority)

    projection_coverage = {
        "seam": {
            "universe_kind": "all_address_interval_states",
            "address_source": "materialization.covering_states",
            "interval_source": "time_axis.boundary_dates",
            "row_count": len(seam_entries),
            "omitted_row_count": 0,
            "blocked_row_count": blocked_row_count,
        }
    }

    # --- artifacts manifest (§4, exhaustive; absent families explicit null) ---
    artifacts = {
        "source_bundle": {
            "schema": D_SOURCE_BUNDLE,
            "root": source_bundle_root,
            "locator": "sources/",
        },
        # §4: REQUIRED index of §3.2 SourceArtifact identity rows; its root
        # IS source_bundle_root (no new root — index and bundle cannot drift).
        "source_artifact_index": {
            "schema": "lawvm.source_artifact_index.v0",
            "root": source_bundle_root,
            "locator": "sources/source_artifacts.json",
        },
        "profile_manifest": {
            "schema": D_STRICT_PROFILE,
            "root": profile_hash,
            "locator": "policy/strict_profile.json",
        },
        "interpretation_policy_manifest": {
            "schema": D_INTERPRETATION_POLICY,
            "root": policy_hash,
            "locator": "policy/interpretation_policy.json",
        },
        "projection_spec_manifest": {
            "schema": D_PROJECTION_SPECS,
            "root": projection_specs_hash,
            "locator": "policy/projection_specs.json",
        },
        "diagnostic_registry_manifest": {
            "schema": D_DIAGNOSTIC_REGISTRY,
            "root": diagnostic_registry_hash,
            "locator": "policy/diagnostic_registry.json",
        },
        # BOOT-01: the engine-derived disposition matrix manifest and the
        # policy-bindings object that aggregates the fold's policy-input roots.
        "disposition_matrix_manifest": {
            "schema": D_DISPOSITION_MATRIX,
            "root": disposition_matrix_hash,
            "locator": "policy/disposition_matrix.json",
        },
        "policy_bindings_manifest": {
            "schema": D_POLICY_BINDINGS,
            "root": policy_bindings_hash,
            "locator": "policy/policy_bindings.json",
        },
        "checker_contract_manifest": {
            "schema": D_CHECKER_CONTRACT,
            "root": checker_contract_hash,
            "locator": "policy/checker_contract.json",
        },
        "base_tree": {
            "schema": D_BASE_TREE,
            "root": base_tree_root,
            "locator": "materialization/base_tree.json",
        },
        "certified_tree_transition_trace": {
            "schema": D_TRACE,
            "root": certified_tree_transition_root,
            "locator": "trace/certified_tree_transitions.jsonl",
        },
        "content_blobs": {
            "schema": D_CONTENT_BLOBS,
            "root": content_blobs_root,
            "locator": "materialization/content_blobs.jsonl",
        },
        "materialization_index": {
            "schema": D_MATERIALIZATION,
            "root": materialization_root,
            "locator": "materialization/state_roots.jsonl",
        },
        "seam_projection_rows": {
            "schema": SEAM_SCHEMA,
            "root": seam_projection_root,
            "locator": "projections/seam_rows.jsonl",
        },
        "dump_projection_rows": None,
        "transition_graph_projection_rows": None,
        "residual_ledger": {
            "schema": D_RESIDUAL_LEDGER,
            "root": residual_root,
            "locator": "residue/residuals.jsonl",
        },
        "finding_ledger": {
            "schema": D_FINDING_LEDGER,
            "root": finding_root,
            "locator": "residue/findings.jsonl",
        },
        "source_unit_coverage": {
            "schema": D_SOURCE_UNIT_COVERAGE,
            "root": source_unit_coverage_root,
            "locator": "coverage/source_unit_coverage.jsonl",
        },
        "potential_operation_coverage": {
            "schema": D_POTENTIAL_OP_COVERAGE,
            "root": potential_op_coverage_root,
            "locator": "coverage/potential_operation_coverage.jsonl",
        },
        "stage_accounts": {
            "schema": D_STAGE_ACCOUNTS,
            "root": stage_accounts_root_value,
            "locator": "stages/stage_accounts.jsonl",
        },
        "apply_authority": {
            "schema": D_APPLY_AUTHORITY,
            "root": apply_authority_root_value,
            "locator": "stages/apply_authority.jsonl",
        },
    }

    roots = {
        "source_bundle_root": source_bundle_root,
        "base_tree_root": base_tree_root,
        "certified_tree_transition_root": certified_tree_transition_root,
        "content_blobs_root": content_blobs_root,
        "materialization_root": materialization_root,
        "projection_root": projection_root,
        "residual_root": residual_root,
        "finding_root": finding_root,
        "coverage_root": coverage_root,
        # Additive per-stage attribution layer (WAIST #9); does NOT fold into the
        # flat residual/finding/coverage roots above (they stay value-identical).
        "stage_accounts_root": stage_accounts_root_value,
        # Additive apply/replay authority subroot (WAIST #7); value-stable for an
        # authorized replay, never folds into the flat roots (0-delta).
        "apply_authority_root": apply_authority_root_value,
        # BOOT-01: the policy-bindings root — the trust root that content-binds
        # the registry/profile/disposition-matrix the certification fold depends
        # on. Committed in `roots` so it folds into certificate_root; a forged
        # policy input changes this and therefore the cert root (Pro §2).
        "policy_bindings_root": policy_bindings_hash,
    }

    envelope: Dict[str, Any] = {
        "schema": CERTIFICATE_SCHEMA,
        "claim_kind": "legal_work_temporal_text_state",
        "subject": {
            "jurisdiction": "fi",
            "work_id": f"fi:act:{canonical_id}",
            "work_kind": "normative_act",
            "local_id": canonical_id,
            "legacy_statute_id": canonical_id,
        },
        "scope": {"kind": "whole_work", "addresses": []},
        "time_scope": time_scope,
        "profile": {"profile_id": PROFILE_ID, "profile_hash": profile_hash},
        "interpretation_policy": {"policy_id": POLICY_ID, "policy_hash": policy_hash},
        "time_axis": {
            "change_dates_root": change_dates_root,
            "min_date": boundary[0],
            "max_date": boundary[-1],
        },
        "roots": roots,
        # BOOT-01 (Pro §3 / item C-3): name the root set honestly so a reader
        # cannot mistake WHICH members `certificate_root` commits. This is the
        # full per-work dossier root (every §3 subroot + the policy-bindings
        # trust root), NOT a sparse minimal-state pack. `root_members` is the
        # authoritative member list; verify_bundle asserts it equals the actual
        # `roots` keys.
        "certificate_root_profile": CERTIFICATE_ROOT_PROFILE,
        "root_members": sorted(roots),
        "certificate_status": certificate_status,
        "residual_summary": residual_summary,
        "projection_coverage": projection_coverage,
        "artifacts": artifacts,
        "checker_contract": checker_contract,
    }
    # §3.3: certificate_root commits to the COMPLETE envelope minus
    # certificate_id; certificate_id is derived from it.
    certificate_root = leaf_hash(D_CERT_ROOT, envelope)
    certificate_id = certificate_root
    envelope_with_id = dict(envelope)
    envelope_with_id["certificate_id"] = certificate_id

    # --- write the bundle ---
    out_path.mkdir(parents=True, exist_ok=True)
    _write_json(out_path / "certificate.json", envelope_with_id)
    for identity in source_identities:
        blob_path = out_path / identity["locator"]
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(source_blobs[identity["source_artifact_id"]])
    _write_json(out_path / "sources" / "source_artifacts.json", source_identities)
    _write_json(out_path / "policy" / "strict_profile.json", profile_manifest)
    _write_json(out_path / "policy" / "interpretation_policy.json", policy_manifest)
    _write_json(out_path / "policy" / "projection_specs.json", projection_specs_manifest)
    _write_json(out_path / "policy" / "diagnostic_registry.json", diagnostic_registry_manifest)
    _write_json(
        out_path / "policy" / "disposition_matrix.json",
        {"schema": D_DISPOSITION_MATRIX, "matrix": disposition_matrix},
    )
    _write_json(out_path / "policy" / "policy_bindings.json", policy_bindings_manifest)
    _write_json(out_path / "policy" / "checker_contract.json", checker_contract_manifest)
    _write_jsonl(out_path / "trace" / "certified_tree_transitions.jsonl", transition_rows)
    (out_path / "trace" / "certified_tree_transitions.root").write_text(
        certified_tree_transition_root + "\n", encoding="utf-8"
    )
    _write_json(out_path / "materialization" / "base_tree.json", base_tree)
    _write_jsonl(out_path / "materialization" / "content_blobs.jsonl", blob_rows)
    _write_jsonl(out_path / "materialization" / "state_roots.jsonl", checkpoint_rows)
    wrapper_rows: List[Dict[str, Any]] = []
    for entry in seam_entries:
        wrapper_rows.append(
            {
                "projection_payload": entry["projection_payload"],
                "certification_status": entry["certification_status"],
                "universe": entry["universe"],
                "certificate": {
                    "certificate_id": certificate_id,
                    "certificate_root": certificate_root,
                    "projection_kind": "lawvm.provision_state",
                    "projection_schema": SEAM_SCHEMA,
                    "projection_spec_version": SEAM_SPEC_VERSION,
                    "projection_spec_hash": seam_spec_hash,
                    "projection_hash": entry["_projection_hash"],
                    "inclusion_path": ["projections/seam_rows.jsonl"],
                },
            }
        )
    _write_jsonl(out_path / "projections" / "seam_rows.jsonl", wrapper_rows)
    _write_jsonl(out_path / "residue" / "residuals.jsonl", residual_rows)
    _write_jsonl(out_path / "residue" / "findings.jsonl", finding_rows)
    _write_jsonl(out_path / "coverage" / "source_unit_coverage.jsonl", source_unit_rows)
    _write_jsonl(out_path / "coverage" / "potential_operation_coverage.jsonl", potential_op_rows)
    _write_jsonl(out_path / "stages" / "stage_accounts.jsonl", stage_account_rows)
    _write_jsonl(
        out_path / "stages" / "apply_authority.jsonl", [apply_authority_row_value]
    )

    # Writer-side self-check: recompute every committed root from the bundle
    # files independently. Not a checker; raises on writer inconsistency.
    verify_bundle(out_path)

    # Register the bundle as a taint-checkable build (consumed_by_build
    # contract): the edges/record live in the persistent provenance graph,
    # AFTER artifact emission, never inside the certificate root (no
    # certificate_root <-> graph cycle).  Recorder failure propagates and
    # fails the emission — the artifact is then not considered published.
    import os

    from lawvm.core.build_consumption import record_build_in_store
    from lawvm.core.provenance_graph import ArtifactRef
    from lawvm.core.provenance_graph_storage import GraphStore

    resolved_graph_root = Path(
        graph_store_root
        or os.environ.get("LAWVM_GRAPH_STORE_ROOT")
        or "data/fi/v1/provenance_graph"
    )
    build_ref = record_build_in_store(
        GraphStore(resolved_graph_root),
        artifact_ref=ArtifactRef(
            artifact_type="certificate_bundle",
            artifact_id=certificate_id,
            content_hash=certificate_root,
        ),
        build_kind="cert",
        # Versioned schema string: "lawvm.certificate.v" + spec version
        # (CERTIFICATE_SCHEMA's bare major "v0" is subsumed by "v0.4.1").
        build_schema=f"lawvm.certificate.v{CERTIFICATE_SPEC_VERSION}",
        consumed_assertion_ids=(),  # the experimental writer admits no manual-claim assertions
        profile_fingerprint=profile_hash,
        source_bundle_hash=source_bundle_root,
        scope={"jurisdiction": "fi", "work_id": f"fi:act:{canonical_id}", "kind": "whole_work"},
        time_scope=dict(time_scope),
    )

    if not quiet:
        for note in notes:
            print(f"[certificate-bundle] note: {note}", flush=True)

    return BundleWriteResult(
        bundle_dir=str(out_path),
        certificate_id=certificate_id,
        build_id=build_ref.build_id,
        certificate_status=certificate_status,
        statute_id=canonical_id,
        title=bundle.title,
        boundary_dates=boundary,
        transition_count=len(transition_rows),
        seam_row_count=len(wrapper_rows),
        residual_count=len(residual_rows),
        finding_count=len(finding_rows),
        roots=dict(roots, certificate_root=certificate_root),
        writer_notes=notes,
    )


# ---------------------------------------------------------------------------
# Writer-side self-check (independent root recomputation; NOT checker v0)
# ---------------------------------------------------------------------------


def _vf_structural_hash(node: Mapping[str, Any]) -> str:
    """Independent §2.2 structural-hash recompute from a content_json dict."""
    h = hashlib.sha256()

    def _rec(n: Mapping[str, Any]) -> None:
        h.update(str(n.get("kind") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(str(n.get("label") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(str(n.get("text") or "").encode("utf-8"))
        h.update(b"\x01")
        for child in n.get("children") or []:
            _rec(child)
        h.update(b"\x02")

    _rec(node)
    return h.hexdigest()


def _vf_covering_state_hash(state: Mapping[str, str]) -> str:
    """Independent §8.1 covering-state hash recompute (bare-hex values)."""
    h = hashlib.sha256()
    for addr in sorted(state):
        h.update(addr.encode("utf-8"))
        h.update(b"\x00")
        h.update(state[addr].encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleSelfCheckError(message)


def verify_bundle(bundle_dir: str | Path) -> Dict[str, str]:
    """Recompute every committed root from bundle contents and compare.

    Writer-side self-check ONLY. This function asserts the writer's internal
    consistency (roots, status algebra, coverage universes recompute from the
    emitted artifacts). It is NOT checker v0, performs no trace-precondition
    replay against claims, and never produces a verdict — a passing
    self-check does not make the bundle a checked certificate.
    """
    bundle_path = Path(bundle_dir)
    envelope = json.loads((bundle_path / "certificate.json").read_text(encoding="utf-8"))
    roots: Dict[str, str] = envelope["roots"]
    artifacts = envelope["artifacts"]
    recomputed: Dict[str, str] = {}

    # WAIST #8 load-bearing branch (FIRST content self-check, before any root
    # recompute): the materialization stage account's coverage must be CLEAN for a
    # clean certificate. A degraded/violating materialization (`coverage.violation
    # > 0` or a blocking residual) makes a clean dossier impossible —
    # `verify_bundle` re-asserts it from the COMMITTED rows so the
    # discarded-coverage failure class cannot be silently certified. Placed first
    # so this branch's specific diagnostic fires before unrelated guards.
    if envelope["certificate_status"] == "clean":
        _verify_materialization_stage_clean(
            _read_jsonl(bundle_path / artifacts["stage_accounts"]["locator"])
        )
        # WAIST #7 firewall recompute: a clean certificate MUST carry a
        # replay_authorized apply-authority row. Re-asserted from the COMMITTED
        # row so a severed authority (dropped to neutral) makes the self-check
        # raise BundleSelfCheckError — the guard-liveness property.
        _verify_apply_authority_clean(
            _read_jsonl(bundle_path / artifacts["apply_authority"]["locator"])
        )

    # apply/replay authority subroot (additive): recompute from the committed row.
    apply_authority_rows = _read_jsonl(
        bundle_path / artifacts["apply_authority"]["locator"]
    )
    _require(
        len(apply_authority_rows) == 1,
        "apply_authority artifact must carry exactly one row "
        f"(found {len(apply_authority_rows)})",
    )
    recomputed["apply_authority_root"] = leaf_hash(
        D_APPLY_AUTHORITY, apply_authority_rows[0]
    )

    # sources
    identities = json.loads(
        (bundle_path / artifacts["source_artifact_index"]["locator"]).read_text(encoding="utf-8")
    )
    for identity in identities:
        data = (bundle_path / identity["locator"]).read_bytes()
        _require(
            _sha256_rendered(data) == identity["raw_source_hash"],
            f"raw_source_hash mismatch for {identity['source_artifact_id']}",
        )
    recomputed["source_bundle_root"] = set_root(
        D_SOURCE_BUNDLE, [leaf_hash(D_SOURCE_ARTIFACT, identity) for identity in identities]
    )

    # base tree
    base_tree = json.loads(
        (bundle_path / artifacts["base_tree"]["locator"]).read_text(encoding="utf-8")
    )
    recomputed["base_tree_root"] = leaf_hash(D_BASE_TREE, base_tree)

    # content blobs: structural hashes recompute from content_json
    blob_rows = _read_jsonl(bundle_path / artifacts["content_blobs"]["locator"])
    blob_hashes: set[str] = set()
    for row in blob_rows:
        bare = row["content_hash"].removeprefix("sha256:")
        _require(
            _vf_structural_hash(row["content_json"]) == bare,
            f"content blob {row['content_hash']} does not recompute from content_json",
        )
        blob_hashes.add(bare)
    recomputed["content_blobs_root"] = set_root(
        D_CONTENT_BLOBS, [leaf_hash(D_CONTENT_BLOB, row) for row in blob_rows]
    )

    # trace: leaf/list roots over the certified core, ordering rules
    transition_rows = _read_jsonl(
        bundle_path / artifacts["certified_tree_transition_trace"]["locator"]
    )
    leaves = []
    prev_seq = 0
    prev_date = ""
    for row in transition_rows:
        _require(row["sequence"] > prev_seq, f"sequence not strictly increasing at {row['transition_id']}")
        _require(
            row["effective_date"] >= prev_date,
            f"effective_date decreasing at {row['transition_id']}",
        )
        prev_seq, prev_date = row["sequence"], row["effective_date"]
        leaves.append(leaf_hash(D_TRANSITION, _certified_core(row)))
    recomputed["certified_tree_transition_root"] = list_root(D_TRACE, leaves)
    root_file = (bundle_path / "trace" / "certified_tree_transitions.root").read_text(
        encoding="utf-8"
    ).strip()
    _require(root_file == recomputed["certified_tree_transition_root"], "trace .root file mismatch")

    # fold the trace from the base tree; recompute checkpoints + universe
    checkpoint_rows = _read_jsonl(bundle_path / artifacts["materialization_index"]["locator"])
    base_units = {u["address"]: u["content_hash"].removeprefix("sha256:") for u in base_tree["units"]}
    state: Dict[str, str] = dict(base_units)
    transitions_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in transition_rows:
        transitions_by_date.setdefault(row["effective_date"], []).append(row)
    boundary = [row["date"] for row in checkpoint_rows]
    _require(boundary == sorted(boundary), "checkpoint rows not in date order")
    declared_dates = set(boundary)
    for row in transition_rows:
        _require(
            row["effective_date"] in declared_dates,
            f"transition {row['transition_id']} effective_date outside declared change dates",
        )
    states_by_date: Dict[str, Dict[str, str]] = {}
    for checkpoint in checkpoint_rows:
        date = checkpoint["date"]
        batch = transitions_by_date.get(date, [])
        touched: set[str] = set()
        for row in batch:
            addr = row["target_address"]
            _require(addr not in touched, f"duplicate target {addr} in date-batch {date}")
            touched.add(addr)
            pre = row["pre_hash"].removeprefix("sha256:") if row["pre_hash"] else ""
            _require(
                state.get(addr, "") == pre,
                f"pre_hash mismatch folding {row['transition_id']}",
            )
            if row["action"] == "set_subtree":
                post = row["post_hash"].removeprefix("sha256:")
                _require(post != "", f"set_subtree with empty post_hash at {row['transition_id']}")
                _require(
                    row["payload_hash"] == row["post_hash"],
                    f"payload_hash != post_hash at {row['transition_id']}",
                )
                _require(post in blob_hashes, f"payload blob missing for {row['transition_id']}")
                state[addr] = post
            elif row["action"] == "delete_subtree":
                _require(row["post_hash"] == "", f"delete_subtree with post_hash at {row['transition_id']}")
                state.pop(addr, None)
            else:
                raise BundleSelfCheckError(f"unknown action {row['action']!r} at {row['transition_id']}")
        _require(
            "sha256:" + _vf_covering_state_hash(state) == checkpoint["tree_hash"],
            f"checkpoint tree_hash mismatch at {date}",
        )
        _require(
            checkpoint["active_unit_count"] == len(state),
            f"checkpoint active_unit_count mismatch at {date}",
        )
        states_by_date[date] = dict(state)
    recomputed["materialization_root"] = list_root(
        D_MATERIALIZATION, [leaf_hash(D_STATE_ROOT, row) for row in checkpoint_rows]
    )

    # time axis
    recomputed_change_dates_root = set_root(D_CHANGE_DATES, boundary)
    _require(
        recomputed_change_dates_root == envelope["time_axis"]["change_dates_root"],
        "change_dates_root mismatch",
    )
    _require(
        envelope["time_axis"]["min_date"] == boundary[0]
        and envelope["time_axis"]["max_date"] == boundary[-1],
        "time_axis min/max do not match committed boundary dates",
    )
    time_scope = envelope["time_scope"]
    _require(time_scope["kind"] == "closed_interval", "experimental writer requires closed_interval")
    _require(
        time_scope["from"] <= boundary[0] and boundary[-1] <= time_scope["to"],
        "boundary dates outside time_scope",
    )

    # seam projection rows: payload hashes, family root, projection_root,
    # parentage consistency, universe reconciliation (§5.5)
    # §3.4: recompute projection hashes under the bundle's OWN pinned
    # hash_excluded_members, never a hardcoded table.
    projection_specs = json.loads(
        (bundle_path / artifacts["projection_spec_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    seam_excluded = projection_specs["projections"]["seam"]["hash_excluded_members"]
    wrapper_rows = _read_jsonl(bundle_path / artifacts["seam_projection_rows"]["locator"])
    projection_hashes = []
    emitted_universe: set[Tuple[str, str]] = set()
    certification_statuses: List[str] = []
    for wrapper in wrapper_rows:
        payload = wrapper["projection_payload"]
        projection_hash = projection_payload_hash(payload, seam_excluded)
        parentage = wrapper["certificate"]
        _require(
            parentage["projection_hash"] == projection_hash,
            "parentage projection_hash does not recompute from payload",
        )
        _require(
            parentage["certificate_id"] == envelope["certificate_id"]
            and parentage["certificate_root"] == envelope["certificate_id"],
            "parentage does not reference this certificate",
        )
        projection_hashes.append(projection_hash)
        universe = wrapper["universe"]
        emitted_universe.add((universe["address"], universe["interval"][0]))
        certification_statuses.append(wrapper["certification_status"])
    recomputed_seam_root = set_root(D_PROJECTION_SEAM, projection_hashes)
    _require(
        artifacts["seam_projection_rows"]["root"] == recomputed_seam_root,
        "seam projection root mismatch",
    )
    _require(
        artifacts["dump_projection_rows"] is None
        and artifacts["transition_graph_projection_rows"] is None,
        "experimental writer emits only the seam family",
    )
    recomputed["projection_root"] = leaf_hash(
        D_PROJECTION_ROOT,
        {"seam": recomputed_seam_root, "dump": None, "transition_graph": None},
    )

    # universe reconciliation: row_count + omitted == recomputed universe size
    universe_pairs: set[Tuple[str, str]] = set()
    for date in boundary:
        for addr in states_by_date[date]:
            universe_pairs.add((addr, date))
    coverage_decl = envelope["projection_coverage"]["seam"]
    _require(
        coverage_decl["row_count"] + coverage_decl["omitted_row_count"] == len(universe_pairs),
        f"projection coverage mismatch: rows {coverage_decl['row_count']} + omitted "
        f"{coverage_decl['omitted_row_count']} != universe {len(universe_pairs)}",
    )
    _require(coverage_decl["row_count"] == len(wrapper_rows), "row_count != emitted rows")
    _require(emitted_universe == universe_pairs, "emitted universe differs from recomputed universe")
    _require(
        coverage_decl["blocked_row_count"]
        == sum(1 for s in certification_statuses if s == "blocked"),
        "blocked_row_count mismatch",
    )

    # residue + findings
    residual_rows = _read_jsonl(bundle_path / artifacts["residual_ledger"]["locator"])
    finding_rows = _read_jsonl(bundle_path / artifacts["finding_ledger"]["locator"])
    recomputed["residual_root"] = set_root(
        D_RESIDUAL_LEDGER, [leaf_hash(D_RESIDUAL_LEDGER, row) for row in residual_rows]
    )
    recomputed["finding_root"] = set_root(
        D_FINDING_LEDGER, [leaf_hash(D_FINDING_LEDGER, row) for row in finding_rows]
    )
    registry = json.loads(
        (bundle_path / artifacts["diagnostic_registry_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    registry_rows = registry["rows"]
    registered_codes = frozenset(row["code"] for row in registry_rows)
    registry_by_code = {row["code"]: row for row in registry_rows}

    # ----- BOOT-01: re-derive the disposition matrix from the ENGINE -----
    # The certification fold's defining policy input is the (code x profile ->
    # disposition) matrix. The forge (Pro §2) is: rewrite the registry so a kind
    # that BLOCKS instead permits, recompute every root, ship clean. We close it
    # by RE-DERIVING the matrix from the engine (FINDING_REGISTRY under the
    # PINNED profile fields) and refusing if the committed registry/matrix
    # disagrees — a row's asserted disposition is NEVER trusted.
    profile_manifest_committed = json.loads(
        (bundle_path / artifacts["profile_manifest"]["locator"]).read_text(encoding="utf-8")
    )
    pinned_profile_fields = profile_manifest_committed["engine_profile"]
    engine_matrix = build_disposition_matrix(pinned_profile_fields)
    # NOTE: disposition_matrix_root is committed via the artifacts manifest and
    # inside policy_bindings, NOT as a top-level `roots` member — keep it out of
    # `recomputed` (which is cross-checked key-for-key against `roots`).
    engine_disposition_matrix_root = disposition_matrix_root(engine_matrix)

    committed_matrix_doc = json.loads(
        (bundle_path / artifacts["disposition_matrix_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    committed_matrix = committed_matrix_doc["matrix"]
    _require(
        committed_matrix == {code: dict(cells) for code, cells in engine_matrix.items()},
        "committed disposition matrix disagrees with the engine re-derivation "
        "(BOOT-01 forged-registry/matrix: a disposition was edited post-hoc)",
    )

    for row in residual_rows:
        code = row["diagnostic_code"]
        _require(code in registered_codes, f"residual carries unregistered code {code!r}")
        _require(
            row["kind"] in registry_by_code[code]["allowed_residual_kinds"],
            f"residual kind {row['kind']!r} not allowed for code {code!r}",
        )
        # §5.4 + BOOT-01: profile_effect is DERIVED. The cached copy must equal
        # the ENGINE-re-derived disposition (not merely the committed registry
        # row) — so a forged registry row cannot license a softened residual.
        engine_disposition = engine_matrix[code][PROFILE_ID]
        _require(
            row["profile_effect"].get(PROFILE_ID) == engine_disposition,
            f"residual profile_effect for {code!r} disagrees with the engine-derived "
            "disposition (BOOT-01)",
        )
        # BOOT-01 (item B): the registry row may not author a disposition out of
        # step with the engine matrix either.
        _require(
            registry_by_code[code]["profile_disposition"][PROFILE_ID] == engine_disposition,
            f"registry profile_disposition for {code!r} disagrees with the engine "
            "matrix (BOOT-01 forged registry)",
        )
    # §5.6: every blocking finding has a residual recording its disposition.
    residual_finding_refs = {ref for row in residual_rows for ref in row["finding_refs"]}
    for row in finding_rows:
        if row["blocking"]:
            _require(
                row["finding_id"] in residual_finding_refs,
                f"blocking finding {row['diagnostic_code']} has no residual row (§5.6)",
            )

    # coverage roots
    source_unit_rows = _read_jsonl(bundle_path / artifacts["source_unit_coverage"]["locator"])
    potential_op_rows = _read_jsonl(
        bundle_path / artifacts["potential_operation_coverage"]["locator"]
    )
    identity_by_id = {identity["source_artifact_id"]: identity for identity in identities}
    for cov_row in source_unit_rows + potential_op_rows:
        anchor = cov_row["source_anchor"]
        identity = identity_by_id.get(anchor["source_artifact_id"])
        _require(identity is not None, f"coverage anchor names unbundled {anchor['source_artifact_id']}")
        data = (bundle_path / anchor["locator"]).read_bytes()
        start, end = anchor["span"]
        _require(
            anchor["span_unit"] == "byte" and 0 <= start <= end <= len(data),
            "coverage anchor span outside source bytes",
        )
        _require(
            _sha256_rendered(data[start:end]) == anchor["quote_hash"],
            "coverage anchor quote_hash mismatch",
        )
    src_cov_root = set_root(
        D_SOURCE_UNIT_COVERAGE, [leaf_hash(D_SOURCE_UNIT_COVERAGE, r) for r in source_unit_rows]
    )
    op_cov_root = set_root(
        D_POTENTIAL_OP_COVERAGE, [leaf_hash(D_POTENTIAL_OP_COVERAGE, r) for r in potential_op_rows]
    )
    recomputed["coverage_root"] = leaf_hash(
        D_COVERAGE,
        {"source_unit_coverage": src_cov_root, "potential_operation_coverage": op_cov_root},
    )

    # per-stage account subroots (WAIST #9): recompute each stage's subroots FROM
    # its committed rows and re-aggregate. A stage account severed before the
    # dossier (or whose subroot disagrees with its rows) diverges here — the
    # guard-liveness property the StageResult endgame demands.
    stage_account_rows = _read_jsonl(bundle_path / artifacts["stage_accounts"]["locator"])
    recomputed["stage_accounts_root"] = _verify_stage_accounts(stage_account_rows)
    # PART 2 status-contribution recompute (ORCH-2): re-count the #5/#10 BLOCKING
    # per-stage residuals FROM the committed rows so the status recompute below
    # honors them. A broken-ref/dropped-universe-member blocking residual forces
    # `blocked` UNCONDITIONALLY (reachable on the blocked FI corpus, genuine (C)).
    verify_extra_blocking = _committed_stage_blocking_residual_count(stage_account_rows)

    # policy manifest hashes
    for key, domain, envelope_hash in (
        ("profile_manifest", D_STRICT_PROFILE, envelope["profile"]["profile_hash"]),
        (
            "interpretation_policy_manifest",
            D_INTERPRETATION_POLICY,
            envelope["interpretation_policy"]["policy_hash"],
        ),
        ("projection_spec_manifest", D_PROJECTION_SPECS, None),
        ("diagnostic_registry_manifest", D_DIAGNOSTIC_REGISTRY, None),
        ("disposition_matrix_manifest", D_DISPOSITION_MATRIX, None),
        ("policy_bindings_manifest", D_POLICY_BINDINGS, None),
        ("checker_contract_manifest", D_CHECKER_CONTRACT, None),
    ):
        manifest = json.loads(
            (bundle_path / artifacts[key]["locator"]).read_text(encoding="utf-8")
        )
        manifest_hash = leaf_hash(domain, manifest)
        _require(manifest_hash == artifacts[key]["root"], f"{key} root mismatch")
        if envelope_hash is not None:
            _require(manifest_hash == envelope_hash, f"{key} hash != envelope commitment")

    # ----- BOOT-01: policy-bindings binds the REAL committed policy roots -----
    # The policy-bindings object's root is the trust root committed in `roots`.
    # Re-assert that (a) every bound root names the ACTUAL committed manifest
    # root, (b) the disposition_matrix_root it binds is the engine re-derivation,
    # and (c) policy_bindings_root recomputes. A forged policy input that did not
    # also rewrite policy_bindings is caught here; one that did rewrite it changes
    # policy_bindings_root -> certificate_root (caught by the cert-root recompute).
    policy_bindings = json.loads(
        (bundle_path / artifacts["policy_bindings_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    _require(
        policy_bindings["diagnostic_registry_root"]
        == artifacts["diagnostic_registry_manifest"]["root"],
        "policy_bindings.diagnostic_registry_root != committed registry root (BOOT-01)",
    )
    _require(
        policy_bindings["profile_manifest_root"] == artifacts["profile_manifest"]["root"],
        "policy_bindings.profile_manifest_root != committed profile root (BOOT-01)",
    )
    _require(
        policy_bindings["disposition_matrix_root"] == engine_disposition_matrix_root,
        "policy_bindings.disposition_matrix_root != engine-derived matrix root (BOOT-01)",
    )
    _require(
        policy_bindings["disposition_matrix_root"]
        == artifacts["disposition_matrix_manifest"]["root"],
        "policy_bindings.disposition_matrix_root != committed matrix manifest root (BOOT-01)",
    )
    _require(
        policy_bindings["source_policy_root"]
        == artifacts["interpretation_policy_manifest"]["root"],
        "policy_bindings.source_policy_root != committed interpretation-policy root (BOOT-01)",
    )
    # DUAL-01 honesty: selection_profile_root is bound absent (no reified
    # selection-profile object) — it must be the explicit null, never a hash.
    _require(
        policy_bindings["selection_profile_root"] is None,
        "policy_bindings.selection_profile_root must be null (no reified object; DUAL-01)",
    )
    recomputed["policy_bindings_root"] = policy_bindings_root(policy_bindings)

    # BOOT-01 (item C-3): certificate_root_profile must name this root set
    # honestly and root_members must equal the ACTUAL committed roots keys, so a
    # reader can never mistake which members certificate_root commits.
    _require(
        envelope.get("certificate_root_profile") == CERTIFICATE_ROOT_PROFILE,
        f"certificate_root_profile {envelope.get('certificate_root_profile')!r} != "
        f"{CERTIFICATE_ROOT_PROFILE!r} (BOOT-01 item C-3)",
    )
    _require(
        envelope.get("root_members") == sorted(roots),
        "root_members does not match the actual committed roots keys (BOOT-01 item C-3)",
    )

    # ----- BOOT-01 (Pro §8 item C) writer-refusal gate -----
    # A clean certificate is FORBIDDEN if any residual blocks under the engine
    # re-derived disposition. We do NOT trust a row's asserted profile_effect:
    # the disposition comes from `engine_matrix`. This re-derives the §5.2 fold's
    # blocking arm from the pinned policy inputs, so a forged registry that
    # softened a real blocker to clean is refused here even before the status
    # recompute.
    if envelope["certificate_status"] == "clean":
        for row in residual_rows:
            code = row["diagnostic_code"]
            if engine_matrix.get(code, {}).get(PROFILE_ID) == "blocks":
                raise BundleSelfCheckError(
                    f"clean certificate carries residual {code!r} that BLOCKS under the "
                    "engine-derived disposition (BOOT-01 writer-refusal)"
                )
    # A blocked certificate must cite at least one blocking residual KIND
    # (item C-2). Reachable on the blocked FI corpus; honors the additive
    # stage-contributed blockers that do not fold into the flat ledger.
    if envelope["certificate_status"] == "blocked":
        cites_blocking = any(
            engine_matrix.get(row["diagnostic_code"], {}).get(PROFILE_ID) == "blocks"
            or row["diagnostic_code"] not in registered_codes
            for row in residual_rows
        )
        _require(
            cites_blocking or verify_extra_blocking > 0,
            "blocked certificate cites no blocking residual kind (BOOT-01 item C-2)",
        )

    # envelope root members vs recomputed
    for name, value in recomputed.items():
        _require(
            roots[name] == value,
            f"envelope root {name} = {roots[name]} but artifacts recompute to {value}",
        )
    # manifest roots must equal the corresponding roots members where both exist (§4)
    for key, root_name in (
        ("source_bundle", "source_bundle_root"),
        ("base_tree", "base_tree_root"),
        ("certified_tree_transition_trace", "certified_tree_transition_root"),
        ("content_blobs", "content_blobs_root"),
        ("materialization_index", "materialization_root"),
        ("residual_ledger", "residual_root"),
        ("finding_ledger", "finding_root"),
        ("stage_accounts", "stage_accounts_root"),
        ("apply_authority", "apply_authority_root"),
    ):
        _require(artifacts[key]["root"] == roots[root_name], f"artifacts.{key}.root != roots.{root_name}")

    # status algebra recompute (§5.2) and summary counts (§5.6)
    recomputed_status = compute_certificate_status(
        residual_rows=residual_rows,
        certification_statuses=certification_statuses,
        registered_codes=registered_codes,
        extra_blocking_residual_count=verify_extra_blocking,
    )
    _require(
        recomputed_status == envelope["certificate_status"],
        f"certificate_status {envelope['certificate_status']!r} != recomputed {recomputed_status!r}",
    )
    summary = envelope["residual_summary"]
    counts = {"blocks": 0, "qualifies": 0, "permits": 0}
    by_kind: Dict[str, int] = {}
    for row in residual_rows:
        counts[residual_effect(row, PROFILE_ID)] += 1
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    _require(
        summary["blocking_count"] == counts["blocks"]
        and summary["qualified_count"] == counts["qualifies"]
        and summary["observation_count"] == counts["permits"]
        and summary["frontier_count"] == by_kind.get("manual_frontier", 0)
        and summary["by_kind"] == dict(sorted(by_kind.items())),
        "residual_summary counts do not recompute from the residual ledger",
    )

    # certificate_root over envelope minus certificate_id (§3.3)
    envelope_without_id = {k: v for k, v in envelope.items() if k != "certificate_id"}
    certificate_root = leaf_hash(D_CERT_ROOT, envelope_without_id)
    _require(
        certificate_root == envelope["certificate_id"],
        "certificate_id does not recompute from envelope minus certificate_id",
    )
    recomputed["certificate_root"] = certificate_root
    return recomputed


# ---------------------------------------------------------------------------
# CLI entry (EXPERIMENTAL)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    statute = getattr(args, "statute", None) or "482/2024"
    out = getattr(args, "out", None)
    granularity = getattr(args, "granularity", DEFAULT_GRANULARITY) or DEFAULT_GRANULARITY
    if not out:
        print("error: --out is required", flush=True)
        raise SystemExit(2)
    result = build_certificate_bundle(
        statute,
        out,
        granularity=granularity,
        quiet=False,
        graph_store_root=getattr(args, "graph_store_root", None),
    )
    print("", flush=True)
    print("  EXPERIMENTAL schema-pressure fixture — NOT a checked certificate.", flush=True)
    print("  No checker exists; do not publish or present as a verified claim.", flush=True)
    print("", flush=True)
    print(f"  statute:            {result.statute_id}  ({result.title})", flush=True)
    print(f"  bundle dir:         {result.bundle_dir}", flush=True)
    print(f"  certificate_id:     {result.certificate_id}", flush=True)
    print(f"  build_id:           {result.build_id}", flush=True)
    print(f"  certificate_status: {result.certificate_status}", flush=True)
    print(f"  boundary dates:     {', '.join(result.boundary_dates)}", flush=True)
    print(f"  transitions:        {result.transition_count}", flush=True)
    print(f"  seam rows:          {result.seam_row_count}", flush=True)
    print(f"  residuals:          {result.residual_count}", flush=True)
    print(f"  findings:           {result.finding_count}", flush=True)
    for name, value in result.roots.items():
        print(f"  {name}: {value}", flush=True)
