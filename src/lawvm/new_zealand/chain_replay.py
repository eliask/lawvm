"""Experimental amendment-chain replay for New Zealand (all families).

This is the first NZ end-to-end replay. Unlike the per-operation dry-run
(:mod:`lawvm.new_zealand.dry_run`) and the per-window dry-run-oracle comparison
(:mod:`lawvm.new_zealand.dry_run_oracle`) — both of which reset to a freshly
parsed archived *before* document for every window — this surface carries a
**single evolving tree** across the whole amendment chain:

1. Enumerate a base work's authorized amendment witnesses across ALL FOUR
   operation families (repeal, text_replace, replace, insert), grouped by
   effective amendment date into ordered transitions (the NZ analogue of
   Norway's ``entries_for_base``). Repeal + text_replace ops come from the
   preflight's per-op replayable rows; replace + insert ops come from the
   operation surface's candidate-target witnesses. The ``--families`` flag
   restricts the enumerated set (``all`` by default; ``repeal`` reproduces the
   original repeal-only baseline for comparison).
2. Start from the **earliest** archived consolidated version of the work. (NZ
   archives point-in-time XML from 2007-09-03 onward, so pre-2007 amendments are
   already baked into that base — they are not re-applied here; an op whose target
   is already absent/tombstoned/missing in the evolving tree is a typed skip,
   never a silent drop.)
3. Apply each transition's authorized ops to the **current** evolving tree in a
   deterministic within-transition family order (repeal -> text_replace ->
   replace -> insert: mutate-in-place families first, then structure-changing
   families, with additive inserts last so they anchor on the freshest sibling
   group). Each op resolves its exact target in the *carried* tree and applies
   the SAME per-op mutation kernel the dry-run uses (repeal ->
   :func:`~lawvm.new_zealand.dry_run._tombstone_node`; text_replace ->
   :func:`~lawvm.new_zealand.dry_run._substitute_node_text`; replace ->
   :func:`~lawvm.new_zealand.dry_run._rebase_replacement_root` subtree swap;
   insert -> anchor-positioned subtree add). The mutated tree carries forward —
   errors and skips accumulate down the chain.
4. At each archived version date, materialize the evolving tree and compare it to
   the archived consolidated **oracle** with the core continuous metric
   :func:`lawvm.core.evidence_support.section_similarity` (so partial op coverage
   produces a *similarity curve*, not a useless binary pass/fail).

This is an **experimental dry-run chain replay with partial coverage**. It never
authorizes actual replay (``replay_claims`` stays ``False``), never mutates the
archive, and never turns the oracle into source truth. It reports similarity, not
a verdict. Every non-applied operation is a typed, visible skip residual: replayed
ops + typed skips = the full op census.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.evidence_support import section_similarity
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dry_run import (
    _amending_act_root,
    _amending_node_by_href,
    _child_nodes_of_kind,
    _derive_insert_anchor,
    _derive_nested_insert_anchor,
    _derive_top_level_insert_anchor,
    _is_before_tree_dependent_insert_label,
    _leaf_source_kind,
    _leaf_source_label,
    _node_digest,
    _occupancy,
    _parse_archived_version,
    _rebase_replacement_root,
    _replayable_repeal_rows,
    _replayable_text_replace_rows,
    _resolve_target_nodes,
    _source_path_for_address,
    _source_path_for_tree_path,
    _substitute_node_text,
    _tombstone_node,
    _top_level_sibling_labels,
)
from lawvm.new_zealand.effect_candidates import (
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import (
    NZSourceDocument,
    NZSourceNode,
    extract_structural_insertion,
    extract_structural_replacement,
)
from lawvm.core.comparison_normalization import normalized_inline_occurrence_count
from lawvm.new_zealand.version_diff import (
    NZArchivedVersion,
    archived_xml_versions_for_work,
)

# Within-transition family apply order. Mutate-in-place families (repeal,
# text_replace) run before structure-changing families (replace), and additive
# inserts run LAST so they anchor on the freshest sibling group a prior op in the
# same transition may have produced. This order is documented and stable; if a
# real case needs a different order, the diverge-loud guard in the driver surfaces
# it (a wrong/mis-ordered op corrupts the tree and DROPS combined similarity at
# that step — which is reported, not hidden).
CHAIN_FAMILY_ORDER: tuple[str, ...] = ("repeal", "text_replace", "replace", "insert")

_ALL_FAMILIES: frozenset[str] = frozenset(CHAIN_FAMILY_ORDER)

# Honesty: this surface is an experimental partial-coverage chain replay. It does
# NOT authorize canonical effect replay anywhere.
NZ_CHAIN_REPLAY_TRUTH_CLAIM = (
    "experimental_dry_run_chain_replay_partial_repeal_coverage_not_canonical"
)
NZ_CHAIN_REPLAY_REPLAY_CLAIMS = False

# Typed skip buckets (mirror Norway's NOReplayResult typed residual lists — never
# a silent drop). One residual per skipped op. The shared buckets apply to every
# family; the family-specific buckets below name the extra ways a non-repeal op
# can fail to apply to the carried tree.
SKIP_UNEXTRACTABLE = "amendment_skipped_unextractable"
SKIP_UNRESOLVED_TARGET = "amendment_skipped_unresolved_target"
SKIP_TARGET_ABSENT = "amendment_skipped_target_absent"
SKIP_AMBIGUOUS_TARGET = "amendment_skipped_ambiguous_target"
SKIP_ALREADY_TOMBSTONED = "amendment_skipped_already_tombstoned"
SKIP_FUTURE = "amendment_skipped_future"
# text_replace: the old_text does not occur exactly once in the CARRIED node, or
# the literal substitution left the node text unchanged.
SKIP_TEXT_OCCURRENCE_MISMATCH = "amendment_skipped_text_occurrence_mismatch"
SKIP_TEXT_APPLY_NO_OP = "amendment_skipped_text_apply_no_op"
# replace/insert: the amending act XML, the cited provision href, or the
# extracted one-to-one payload could not be resolved.
SKIP_AMENDING_UNRESOLVED = "amendment_skipped_amending_work_unresolved"
SKIP_PAYLOAD_NOT_EXTRACTABLE = "amendment_skipped_payload_not_extractable"
# replace: the rebased subtree equals the carried target subtree (vacuous swap).
SKIP_REPLACE_APPLY_NO_OP = "amendment_skipped_replace_apply_no_op"
# insert: the new node is already present, the anchor could not be derived, or the
# anchor/parent does not resolve uniquely in the carried tree.
SKIP_INSERT_ALREADY_PRESENT = "amendment_skipped_insert_target_already_present"
SKIP_INSERT_ANCHOR_NOT_DERIVABLE = "amendment_skipped_insert_anchor_not_derivable"
SKIP_INSERT_ANCHOR_UNRESOLVED = "amendment_skipped_insert_anchor_unresolved"
# Family-D closed 2026-06-27: def-term CASE FOLD collision -- the INSERT op's
# leaf-kind is ``def-para`` and the source-path's leaf segment ``def-para:term``
# does NOT match exactly any carried-tree def-para path, BUT a case-alternative
# match DOES exist at the same parent path. Per AGENTS §1.4 (no silent sibling
# absorption by label-text equality or case-touch alone):
#
#   the chain EARLIEST archived snapshot (2007-09-03 on act_public_1956_47)
#   carries ``def-para:Subsidiary`` (cap); the op's amending directive XML
#   carries the same definition term in lowercase (``def-para:subsidiary``);
#   the literal path lookup misses (case mismatch on the leaf label) → the
#   insert-already-present skip GATE does not fire → the op APPLIES → the
#   carried-tree ends up with BOTH cap+lowercase variants → the op-local
#   divergence check correctly fires local_similarity=0.0 vs the on-or-after
#   oracle (which carries only ONE variant -- the editorial consolidation
#   collapsed the cap variant later).
#
# The right fix is NOT a def-para removal/merger (that would silently absorb
# per §1.4). It is a typed skip receipt (this bucket + rule_id) emitting the
# absorption evidence so the skip is auditable under its own rule_id rather
# than silently dismissed as the generic insert-already-present bucket. The
# absorption is owned by the named recovery rule ``target_resolution_recovery``
# (per AGENTS §2.1 family tag) with scope_confidence ``inferred_from_payload``
# (per AGENTS §2.2 -- the case-alternative match was inferred from the op's
# amending-payload source, not explicitly named in source as a case-fold).
SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION = (
    "amendment_skipped_insert_def_term_case_fold_collision"
)

_SKIP_RULE_ID: dict[str, str] = {
    SKIP_UNEXTRACTABLE: "nz_chain_replay_op_unextractable_no_source_path",
    SKIP_UNRESOLVED_TARGET: "nz_chain_replay_op_target_resolution_not_exact",
    SKIP_TARGET_ABSENT: "nz_chain_replay_target_absent_in_evolving_tree",
    SKIP_AMBIGUOUS_TARGET: "nz_chain_replay_target_ambiguous_in_evolving_tree",
    SKIP_ALREADY_TOMBSTONED: "nz_chain_replay_target_already_tombstoned_in_evolving_tree",
    SKIP_FUTURE: "nz_chain_replay_effective_date_after_latest_archived_version",
    SKIP_TEXT_OCCURRENCE_MISMATCH: "nz_chain_replay_text_old_text_not_single_occurrence_in_evolving_tree",
    SKIP_TEXT_APPLY_NO_OP: "nz_chain_replay_text_apply_left_node_unchanged",
    SKIP_AMENDING_UNRESOLVED: "nz_chain_replay_amending_work_or_provision_href_unresolved",
    SKIP_PAYLOAD_NOT_EXTRACTABLE: "nz_chain_replay_amending_payload_not_extractable",
    SKIP_REPLACE_APPLY_NO_OP: "nz_chain_replay_replace_apply_left_subtree_unchanged",
    SKIP_INSERT_ALREADY_PRESENT: "nz_chain_replay_insert_target_already_present_in_evolving_tree",
    SKIP_INSERT_ANCHOR_NOT_DERIVABLE: "nz_chain_replay_insert_anchor_not_derivable_from_label_or_siblings",
    SKIP_INSERT_ANCHOR_UNRESOLVED: "nz_chain_replay_insert_anchor_or_parent_not_unique_in_evolving_tree",
    SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION: (
        "nz_chain_replay_insert_def_term_case_fold_collision_recognized"
    ),
}


@dataclass(frozen=True)
class NZChainSkip:
    """One typed, non-silent skip of an authorized op."""

    bucket: str
    rule_id: str
    family: str
    row_id: str
    amendment_date_iso: str
    amending_work_id: str
    source_path: tuple[str, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "rule_id": self.rule_id,
            "family": self.family,
            "row_id": self.row_id,
            "amendment_date_iso": self.amendment_date_iso,
            "amending_work_id": self.amending_work_id,
            "source_path": list(self.source_path),
        }


@dataclass(frozen=True)
class NZChainOp:
    """One authorized amendment witness in the chain (an enumerated transition op).

    ``family`` is one of :data:`CHAIN_FAMILY_ORDER`. The payload fields are
    family-specific: repeal/text_replace carry the canonical ``LegalOperation``
    (text substitutions read their old/new text from its ``text_patch``);
    replace/insert carry the amending act id + cited provision href, from which
    the apply kernel re-extracts the one-to-one structural payload. ``source_path``
    is the inserted/target node's own source-tree path; for an INSERT it is where
    the new node will live (its presence in the carried tree is checked, not its
    resolution).
    """

    family: str
    row_id: str
    amendment_date_iso: str
    amending_work_id: str
    source_path: tuple[str, ...] | None
    target_resolution_status: str
    operation: Any = None  # LegalOperation for repeal/text_replace; None otherwise
    amending_provision_href: str = ""  # replace/insert: cited amending provision


# Back-compat alias: the original repeal-only surface exported ``NZChainRepealOp``.
NZChainRepealOp = NZChainOp


@dataclass(frozen=True)
class NZChainTransition:
    """All authorized ops effective on one amendment date, family- then id-ordered."""

    amendment_date_iso: str
    ops: tuple[NZChainOp, ...]

    @property
    def n_ops(self) -> int:
        return len(self.ops)


@dataclass(frozen=True)
class NZChainSimilarityPoint:
    """Replayed-vs-oracle similarity at one archived version date.

    Two parallel tracks are reported so a path-key artifact is not mistaken for a
    replay error:

    - **raw** (``combined_similarity`` / ``path_jaccard``): paths compared by
      their exact source path. NZ falls back to a positional ``kind#ordinal`` or
      identity ``kind@xml_id`` segment when a node has no stable label; those
      segments churn across consolidations even with zero real change, so the raw
      Jaccard understates agreement.
    - **stable** (``combined_similarity_stable`` / ``path_jaccard_stable``):
      paths compared after collapsing every ``#ordinal`` / ``@xml_id`` segment to
      its bare ``kind``, so position/id churn on otherwise-identical nodes does
      not split a path. This isolates real structural + text divergence.

    ``combined_similarity`` is the FI-style score: the mean of
    ``section_similarity`` over the *union* of paths, where a path present in only
    one document scores 0. ``shared_mean_similarity`` is the mean over shared raw
    paths only. The running skip and apply counts up to and including this
    version's transitions are carried so the curve is read alongside how much
    coverage produced it.
    """

    version_id: str
    version_date: str
    combined_similarity: float
    shared_mean_similarity: float
    path_jaccard: float
    combined_similarity_stable: float
    path_jaccard_stable: float
    replayed_node_count: int
    oracle_node_count: int
    shared_path_count: int
    transitions_applied_so_far: int
    repeals_applied_so_far: int
    repeals_skipped_so_far: int

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version_date": self.version_date,
            "combined_similarity": round(self.combined_similarity, 6),
            "shared_mean_similarity": round(self.shared_mean_similarity, 6),
            "path_jaccard": round(self.path_jaccard, 6),
            "combined_similarity_stable": round(self.combined_similarity_stable, 6),
            "path_jaccard_stable": round(self.path_jaccard_stable, 6),
            "replayed_node_count": self.replayed_node_count,
            "oracle_node_count": self.oracle_node_count,
            "shared_path_count": self.shared_path_count,
            "transitions_applied_so_far": self.transitions_applied_so_far,
            "repeals_applied_so_far": self.repeals_applied_so_far,
            "repeals_skipped_so_far": self.repeals_skipped_so_far,
        }


@dataclass(frozen=True)
class NZChainPerFamilyStat:
    """Applied/skipped + oracle agreement for one operation family in the chain."""

    family: str
    enumerated: int
    applied: int
    skipped: int
    oracle_agreements: int
    oracle_disagreements: int

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "enumerated": self.enumerated,
            "applied": self.applied,
            "skipped": self.skipped,
            "oracle_agreements": self.oracle_agreements,
            "oracle_disagreements": self.oracle_disagreements,
        }


@dataclass(frozen=True)
class NZChainDivergence:
    """A LOUD finding: a content-producing op yielded a node the oracle contradicts.

    A wrong op that corrupts the evolving tree is worse than a skipped op. This is
    the op-LOCAL honesty guard: when a content-producing op (text_replace /
    replace / insert) is applied, the node it produces at its target path is
    compared against the oracle node at that path in the version that should
    reflect the change (the earliest archived version on-or-after the op's
    effective date). A LOW local similarity means the op produced wrong content
    (mis-extracted payload, wrong target, bad anchor) — surfaced here, the
    highest-value finding for the next cycle.

    The op-local check (rather than a transition-level combined-similarity delta)
    deliberately avoids the repeal-only structural-lag confound: a *correct* op
    can lower the GLOBAL combined similarity simply because the carried tree falls
    further behind the oracle's restructuring elsewhere, which is NOT a wrong op.
    Locality isolates the op's own effect.
    """

    family: str
    row_id: str
    target_path: tuple[str, ...]
    oracle_version_id: str
    oracle_version_date: str
    local_similarity: float

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "row_id": self.row_id,
            "target_path": list(self.target_path),
            "oracle_version_id": self.oracle_version_id,
            "oracle_version_date": self.oracle_version_date,
            "local_similarity": round(self.local_similarity, 6),
        }


@dataclass(frozen=True)
class NZChainReplayReport:
    work_id: str
    operation_family: str
    base_version_id: str
    base_version_date: str
    n_archived_versions: int
    transitions: tuple[NZChainTransition, ...]
    similarity_curve: tuple[NZChainSimilarityPoint, ...]
    repeals_applied: int
    repeals_skipped: int
    oracle_tombstone_agreements: int
    oracle_tombstone_disagreements: int
    skips: tuple[NZChainSkip, ...]
    per_family_stats: tuple[NZChainPerFamilyStat, ...] = ()
    divergences: tuple[NZChainDivergence, ...] = ()
    families_requested: tuple[str, ...] = CHAIN_FAMILY_ORDER
    truth_claim: str = NZ_CHAIN_REPLAY_TRUTH_CLAIM
    replay_claims: bool = NZ_CHAIN_REPLAY_REPLAY_CLAIMS

    def skip_bucket_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for skip in self.skips:
            counts[skip.bucket] = counts.get(skip.bucket, 0) + 1
        return dict(sorted(counts.items()))

    def final_similarity(self) -> NZChainSimilarityPoint | None:
        return self.similarity_curve[-1] if self.similarity_curve else None

    def summary(self) -> dict[str, Any]:
        final = self.final_similarity()
        total_repeal_ops = sum(t.n_ops for t in self.transitions)
        return {
            "work_id": self.work_id,
            "operation_family": self.operation_family,
            "truth_claim": self.truth_claim,
            "replay_claims": self.replay_claims,
            "base_version_id": self.base_version_id,
            "base_version_date": self.base_version_date,
            "n_archived_versions": self.n_archived_versions,
            "n_transitions": len(self.transitions),
            "total_repeal_ops": total_repeal_ops,
            "repeals_applied": self.repeals_applied,
            "repeals_skipped": self.repeals_skipped,
            "skip_bucket_counts": self.skip_bucket_counts(),
            "oracle_tombstone_agreements": self.oracle_tombstone_agreements,
            "oracle_tombstone_disagreements": self.oracle_tombstone_disagreements,
            "final_combined_similarity": (
                round(final.combined_similarity, 6) if final is not None else None
            ),
            "final_shared_mean_similarity": (
                round(final.shared_mean_similarity, 6) if final is not None else None
            ),
            "final_path_jaccard": round(final.path_jaccard, 6) if final is not None else None,
            "final_combined_similarity_stable": (
                round(final.combined_similarity_stable, 6) if final is not None else None
            ),
            "final_path_jaccard_stable": (
                round(final.path_jaccard_stable, 6) if final is not None else None
            ),
            "similarity_curve_points": len(self.similarity_curve),
            "families_requested": list(self.families_requested),
            "per_family_stats": [stat.to_jsonable() for stat in self.per_family_stats],
            "n_divergences": len(self.divergences),
        }

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "experimental_dry_run_chain_replay",
            "truth_claim": self.truth_claim,
            "replay_claims": self.replay_claims,
            "summary": self.summary(),
            "similarity_curve": [point.to_jsonable() for point in self.similarity_curve],
            "divergences": [div.to_jsonable() for div in self.divergences],
        }
        if not summary_only:
            payload["transitions"] = [
                {
                    "amendment_date_iso": transition.amendment_date_iso,
                    "n_ops": transition.n_ops,
                    "ops": [
                        {
                            "family": op.family,
                            "row_id": op.row_id,
                            "amending_work_id": op.amending_work_id,
                            "source_path": list(op.source_path) if op.source_path else None,
                            "target_resolution_status": op.target_resolution_status,
                        }
                        for op in transition.ops
                    ],
                }
                for transition in self.transitions
            ]
            payload["skips"] = [skip.to_jsonable() for skip in self.skips]
        return payload


# --- Chain enumeration (NZ analogue of Norway's entries_for_base) ---


def _enumerate_repeal_ops(preflight: NZEffectCandidatePreflightReport) -> list[NZChainOp]:
    """Repeal transition ops from the preflight's replayable repeal rows."""

    ops: list[NZChainOp] = []
    for row in _replayable_repeal_rows(preflight):
        operation = row.operation
        source_path = _source_path_for_address(operation) if operation is not None else None
        ops.append(
            NZChainOp(
                family="repeal",
                row_id=row.row_id,
                amendment_date_iso=row.amendment_date_iso or "",
                amending_work_id=row.amending_work_id,
                source_path=source_path,
                target_resolution_status=row.latest_oracle_target_resolution_status or "",
                operation=operation,
            )
        )
    return ops


def _enumerate_text_replace_ops(preflight: NZEffectCandidatePreflightReport) -> list[NZChainOp]:
    """text_replace transition ops from the preflight's replayable substitutions."""

    ops: list[NZChainOp] = []
    for row in _replayable_text_replace_rows(preflight):
        operation = row.operation
        source_path = _source_path_for_address(operation) if operation is not None else None
        ops.append(
            NZChainOp(
                family="text_replace",
                row_id=row.row_id,
                amendment_date_iso=row.amendment_date_iso or "",
                amending_work_id=row.amending_work_id,
                source_path=source_path,
                target_resolution_status=row.latest_oracle_target_resolution_status or "",
                operation=operation,
            )
        )
    return ops


def _enumerate_structural_ops(surface: Any, family: str) -> list[NZChainOp]:
    """replace/insert transition ops from the operation surface's candidate rows.

    The eligibility filter mirrors the per-op dry-run's witness-row selection
    (:func:`_replace_witness_rows` / :func:`_insert_witness_rows`): a same-family
    witness with a candidate (exact) target address. Payload extractability,
    amending-work resolution, and target/anchor presence in the carried tree are
    checked inside the apply kernel as typed skips, never here — so a witness that
    fails them is still enumerated (attempted), never hidden.
    """

    from lawvm.new_zealand.dry_run import (
        _NZ_INSERT_OPERATION_FAMILIES,
        _NZ_REPLACE_OPERATION_FAMILIES,
    )

    family_set = (
        _NZ_REPLACE_OPERATION_FAMILIES if family == "replace" else _NZ_INSERT_OPERATION_FAMILIES
    )
    ops: list[NZChainOp] = []
    for row in surface.rows:
        if row.operation_family not in family_set:
            continue
        if row.target_address_candidate.target_address_status != "candidate":
            continue
        source_path = _source_path_for_tree_path(row.target_address_candidate.path)
        href = row.amending_provision_hrefs[0] if row.amending_provision_hrefs else ""
        ops.append(
            NZChainOp(
                family=family,
                row_id=row.row_id,
                amendment_date_iso=row.amendment_date_iso or "",
                amending_work_id=row.amending_work_id,
                source_path=source_path,
                target_resolution_status="exact_source_path",  # candidate-target by filter
                amending_provision_href=href,
            )
        )
    return ops


def _bucket_ops_into_transitions(ops: list[NZChainOp]) -> tuple[NZChainTransition, ...]:
    """Bucket enumerated ops by ISO amendment date into ordered transitions.

    Within a date the ops are ordered by ``CHAIN_FAMILY_ORDER`` first (so the
    documented within-transition apply order is realized purely by emission
    order), then by ``(amending_work_id, row_id)`` for determinism. An op with no
    effective date lands in a degenerate empty-date transition so the census still
    counts it (a real gap, never a silent drop).
    """

    family_rank = {family: index for index, family in enumerate(CHAIN_FAMILY_ORDER)}
    by_date: dict[str, list[NZChainOp]] = {}
    for op in ops:
        by_date.setdefault(op.amendment_date_iso, []).append(op)

    transitions: list[NZChainTransition] = []
    for amendment_date_iso in sorted(by_date):
        ordered = sorted(
            by_date[amendment_date_iso],
            key=lambda op: (family_rank.get(op.family, 99), op.amending_work_id, op.row_id),
        )
        transitions.append(
            NZChainTransition(amendment_date_iso=amendment_date_iso, ops=tuple(ordered))
        )
    return tuple(transitions)


def build_nz_chain(
    preflight: NZEffectCandidatePreflightReport,
    surface: Any | None,
    *,
    families: frozenset[str] = _ALL_FAMILIES,
) -> tuple[NZChainTransition, ...]:
    """Enumerate a base work's authorized ops across the requested families.

    Repeal + text_replace ops come from the preflight's replayable rows; replace +
    insert ops come from the operation ``surface``'s candidate-target witnesses
    (``surface`` may be ``None`` when only preflight-sourced families are
    requested). Ops are bucketed by ``amendment_date_iso`` into ISO-date-ordered
    transitions; within a date they follow :data:`CHAIN_FAMILY_ORDER`.
    """

    ops: list[NZChainOp] = []
    if "repeal" in families:
        ops.extend(_enumerate_repeal_ops(preflight))
    if "text_replace" in families:
        ops.extend(_enumerate_text_replace_ops(preflight))
    if "replace" in families and surface is not None:
        ops.extend(_enumerate_structural_ops(surface, "replace"))
    if "insert" in families and surface is not None:
        ops.extend(_enumerate_structural_ops(surface, "insert"))
    return _bucket_ops_into_transitions(ops)


def build_nz_repeal_chain(
    preflight: NZEffectCandidatePreflightReport,
) -> tuple[NZChainTransition, ...]:
    """Enumerate a base work's authorized repeal ops as date-ordered transitions.

    Back-compat repeal-only enumeration (the original surface). Equivalent to
    :func:`build_nz_chain` restricted to the ``repeal`` family. Per-op
    authorization comes from the preflight's replayable repeal rows
    (:func:`_replayable_repeal_rows`) — the SAME set the per-op dry-run consumes,
    but NOT gated on the whole-work ``ready_for_dry_run_replay`` readiness.
    """

    return build_nz_chain(preflight, None, families=frozenset({"repeal"}))


# --- Sequential apply on one evolving tree ---


def _rebase_descendants(
    payload_root: NZSourceNode,
    payload_descendants: tuple[NZSourceNode, ...],
    resolved_path: tuple[str, ...],
) -> list[NZSourceNode]:
    """Re-root a payload subtree's descendants onto the resolved target path.

    The extracted payload carries placeholder ``amend/...`` paths: the root is at
    ``payload_root.path`` (``("amend",)``) and each descendant nests under it. The
    per-op dry-run only ever re-roots the root (its oracle comparison is
    signature-based and path-relative); the chain replay must place the WHOLE
    subtree in the carried document, so every descendant's leading
    ``payload_root.path`` prefix is swapped for ``resolved_path`` while the rest of
    the path (the descendant's position within the subtree) is preserved. Node
    content is otherwise identical — same boring re-root the kernel uses.
    """

    root_prefix = payload_root.path
    depth = len(root_prefix)
    rebased: list[NZSourceNode] = []
    for node in payload_descendants:
        if node.path[:depth] == root_prefix:
            new_path = resolved_path + node.path[depth:]
        else:
            # Defensive: a descendant not under the payload root prefix cannot be
            # rebased deterministically; keep its own path so it stays addressable
            # rather than silently colliding with the target.
            new_path = node.path
        rebased.append(
            NZSourceNode(
                kind=node.kind,
                path=new_path,
                xml_id=node.xml_id,
                xml_path=node.xml_path,
                source_zone=node.source_zone,
                label=node.label,
                heading=node.heading,
                deletion_status=node.deletion_status,
                text=node.text,
                history=node.history,
            )
        )
    return rebased


@dataclass
class _EvolvingTree:
    """A mutable wrapper over the carried document for in-chain amendments.

    Every mutation method calls the SAME boring node-level kernel the per-op
    dry-run uses (``_tombstone_node`` / ``_substitute_node_text`` /
    ``_rebase_replacement_root``); only the *placement* in the carried document is
    re-implemented here (the dry-run resolves against a fresh before-tree, this
    resolves against the carried evolving tree). Document order is preserved.
    """

    document: NZSourceDocument

    def _rebuild(self, nodes: list[NZSourceNode]) -> None:
        self.document = NZSourceDocument(
            xml_locator=self.document.xml_locator,
            version_id=self.document.version_id,
            metadata=self.document.metadata,
            nodes=tuple(nodes),
            document_history=self.document.document_history,
        )

    def node_index(self) -> dict[tuple[str, ...], NZSourceNode]:
        # Document order is preserved; on a duplicate path the first wins for
        # lookup (the resolver below enforces exactly-one anyway).
        index: dict[tuple[str, ...], NZSourceNode] = {}
        for node in self.document.nodes:
            index.setdefault(node.path, node)
        return index

    def tombstone(self, target_path: tuple[str, ...]) -> None:
        new_nodes: list[NZSourceNode] = []
        for node in self.document.nodes:
            if node.path == target_path and not node.deletion_status:
                new_nodes.append(_tombstone_node(node))
            else:
                new_nodes.append(node)
        self._rebuild(new_nodes)

    def substitute_text(self, target_path: tuple[str, ...], old_text: str, new_text: str) -> None:
        """Single-occurrence text substitution on the node at ``target_path``."""

        new_nodes: list[NZSourceNode] = []
        for node in self.document.nodes:
            if node.path == target_path:
                new_nodes.append(_substitute_node_text(node, old_text, new_text))
            else:
                new_nodes.append(node)
        self._rebuild(new_nodes)

    def swap_subtree(
        self,
        target_path: tuple[str, ...],
        replacement_root: NZSourceNode,
        replacement_descendants: tuple[NZSourceNode, ...],
    ) -> None:
        """Replace the target node + its subtree with the rebased replacement subtree.

        The target node's whole subtree (the node at ``target_path`` plus every
        descendant under it) is removed and the rebased replacement subtree (root
        re-rooted onto ``target_path`` plus its descendants re-rooted under it) is
        inserted in its place, preserving document order at the target's position.
        """

        depth = len(target_path)
        rebased_root = _rebase_replacement_root(replacement_root, target_path)
        rebased_subtree = [rebased_root, *_rebase_descendants(
            replacement_root, replacement_descendants, target_path
        )]
        new_nodes: list[NZSourceNode] = []
        inserted = False
        for node in self.document.nodes:
            is_target = node.path == target_path
            is_descendant = len(node.path) > depth and node.path[:depth] == target_path
            if is_target:
                new_nodes.extend(rebased_subtree)
                inserted = True
                continue
            if is_descendant:
                continue  # dropped; replaced by the rebased subtree above
            new_nodes.append(node)
        if not inserted:
            # Defensive: target vanished between resolution and apply. Surface by
            # leaving the tree unchanged (the caller's no-op guard catches it).
            return
        self._rebuild(new_nodes)

    def insert_node(
        self,
        anchor_path: tuple[str, ...],
        direction: str,
        new_node_path: tuple[str, ...],
        payload_root: NZSourceNode,
        payload_descendants: tuple[NZSourceNode, ...],
    ) -> None:
        """Add the rebased payload subtree adjacent to the anchor node.

        The new node lands immediately AFTER the anchor (and its descendants, for
        ``direction == "after"``) or immediately BEFORE the anchor (for
        ``direction == "before"``) in document order, addressed at
        ``new_node_path`` (same parent stem as the anchor). Pre-existing nodes are
        not mutated — insertion only adds the new subtree.
        """

        anchor_depth = len(anchor_path)
        rebased_root = _rebase_replacement_root(payload_root, new_node_path)
        rebased_subtree = [rebased_root, *_rebase_descendants(
            payload_root, payload_descendants, new_node_path
        )]
        nodes = list(self.document.nodes)
        new_nodes: list[NZSourceNode] = []
        i = 0
        n = len(nodes)
        while i < n:
            node = nodes[i]
            if node.path == anchor_path:
                if direction == "before":
                    # Land the new subtree immediately before the anchor node.
                    new_nodes.extend(rebased_subtree)
                    new_nodes.append(node)
                    i += 1
                    continue
                # "after": land the new subtree after the anchor's whole subtree so
                # the inserted provision follows the entire anchor provision in
                # document order.
                new_nodes.append(node)
                j = i + 1
                while (
                    j < n
                    and len(nodes[j].path) > anchor_depth
                    and nodes[j].path[:anchor_depth] == anchor_path
                ):
                    new_nodes.append(nodes[j])
                    j += 1
                new_nodes.extend(rebased_subtree)
                i = j
                continue
            new_nodes.append(node)
            i += 1
        self._rebuild(new_nodes)


@dataclass
class _AppliedOp:
    """One op applied to the carried tree this transition (for divergence finding)."""

    family: str
    row_id: str
    target_path: tuple[str, ...]
    amendment_date_iso: str = ""


_BASE_WORK_ID_RE = re.compile(r"^act_public_(?P<year>\d{4})_(?P<number>[0-9A-Za-z]+)$")


def _base_work_year_number(work_id: str) -> tuple[str, str]:
    """Parse a base ``act_public_{year}_{number}`` work id into (year, number).

    Returns ``("", "")`` for any other work-id shape. The number is normalized the
    same way :func:`parse_public_act_citation` normalizes a schedule-group heading
    citation (leading zeros stripped) so the two compare exactly when keying a
    schedule amendment group to the base act.
    """
    match = _BASE_WORK_ID_RE.match(work_id or "")
    if match is None:
        return ("", "")
    number = match.group("number")
    return (match.group("year"), number.lstrip("0") or "0")


def _apply_transition(
    tree: _EvolvingTree,
    transition: NZChainTransition,
    *,
    latest_version_date: str,
    archive: Any = None,
    amending_root_cache: dict[str, Any] | None = None,
    base_work_year: str = "",
    base_work_number: str = "",
) -> tuple[int, list[NZChainSkip], list[_AppliedOp]]:
    """Apply one transition's authorized ops (all families) to the evolving tree.

    Ops are applied in the order they appear in ``transition.ops``, which the
    enumerator emits in :data:`CHAIN_FAMILY_ORDER`. Returns ``(applied_count,
    skips, applied_ops)``. Every op that cannot be applied is a typed skip, never
    a silent drop. ``archive`` + ``amending_root_cache`` are required only for the
    structural families (replace/insert) that re-extract the amending-act payload;
    a repeal/text_replace-only transition tolerates ``archive=None``.
    ``base_work_year``/``base_work_number`` identify the act being replayed so a
    schedule-indirection amendment can be keyed to its schedule amendment group.
    """

    if amending_root_cache is None:
        amending_root_cache = {}

    applied = 0
    skips: list[NZChainSkip] = []
    applied_ops: list[_AppliedOp] = []

    for op in transition.ops:
        if op.amendment_date_iso and latest_version_date and op.amendment_date_iso > latest_version_date:
            skips.append(_skip(SKIP_FUTURE, op))
            continue
        if op.source_path is None:
            skips.append(_skip(SKIP_UNEXTRACTABLE, op))
            continue

        if op.family == "repeal":
            result = _apply_repeal_op(tree, op)
        elif op.family == "text_replace":
            result = _apply_text_replace_op(tree, op)
        elif op.family == "replace":
            result = _apply_replace_op(
                tree, op, archive, amending_root_cache,
                base_work_year=base_work_year, base_work_number=base_work_number,
            )
        elif op.family == "insert":
            result = _apply_insert_op(
                tree, op, archive, amending_root_cache,
                base_work_year=base_work_year, base_work_number=base_work_number,
            )
        else:
            skips.append(_skip(SKIP_UNEXTRACTABLE, op))
            continue

        if isinstance(result, NZChainSkip):
            skips.append(result)
            continue
        applied += 1
        applied_ops.append(result)

    return applied, skips, applied_ops


def _apply_repeal_op(tree: _EvolvingTree, op: NZChainOp) -> _AppliedOp | NZChainSkip:
    assert op.source_path is not None
    if op.target_resolution_status and op.target_resolution_status != "exact_source_path":
        return _skip(SKIP_UNRESOLVED_TARGET, op)
    matches = _resolve_target_nodes(tree.document, op.source_path)
    if len(matches) == 0:
        # Target not in the carried tree: already baked into the pre-2007 base,
        # removed by an earlier transition, or carry-forward drift. Honest skip.
        return _skip(SKIP_TARGET_ABSENT, op)
    if len(matches) > 1:
        return _skip(SKIP_AMBIGUOUS_TARGET, op)
    target = matches[0]
    if _occupancy(target) != "substantive":
        return _skip(SKIP_ALREADY_TOMBSTONED, op)
    tree.tombstone(target.path)
    return _AppliedOp(family="repeal", row_id=op.row_id, target_path=target.path, amendment_date_iso=op.amendment_date_iso)


def _apply_text_replace_op(tree: _EvolvingTree, op: NZChainOp) -> _AppliedOp | NZChainSkip:
    assert op.source_path is not None
    operation = op.operation
    if operation is None or operation.text_patch is None or operation.text_patch.replacement is None:
        return _skip(SKIP_UNEXTRACTABLE, op)
    if op.target_resolution_status and op.target_resolution_status != "exact_source_path":
        return _skip(SKIP_UNRESOLVED_TARGET, op)
    matches = _resolve_target_nodes(tree.document, op.source_path)
    if len(matches) == 0:
        return _skip(SKIP_TARGET_ABSENT, op)
    if len(matches) > 1:
        return _skip(SKIP_AMBIGUOUS_TARGET, op)
    target = matches[0]
    if _occupancy(target) != "substantive":
        return _skip(SKIP_ALREADY_TOMBSTONED, op)

    patch = operation.text_patch
    old_text = patch.selector.match_text
    new_text = patch.replacement
    # Single-occurrence precondition checked against the CARRIED node text (mirror
    # the dry-run kernel, which checks the before-tree node text). Zero or many is
    # a typed skip — the kernel never guesses which occurrence to edit.
    if normalized_inline_occurrence_count(target.text, old_text) != 1:
        return _skip(SKIP_TEXT_OCCURRENCE_MISMATCH, op)
    new_node_text = target.text.replace(old_text, new_text, 1)
    if new_node_text == target.text:
        # Normalized count found one occurrence but the literal old_text did not
        # change the node — surface loudly rather than emit a vacuous mutation.
        return _skip(SKIP_TEXT_APPLY_NO_OP, op)
    tree.substitute_text(target.path, old_text, new_text)
    return _AppliedOp(family="text_replace", row_id=op.row_id, target_path=target.path, amendment_date_iso=op.amendment_date_iso)


def _apply_replace_op(
    tree: _EvolvingTree,
    op: NZChainOp,
    archive: Any,
    amending_root_cache: dict[str, Any],
    *,
    base_work_year: str = "",
    base_work_number: str = "",
) -> _AppliedOp | NZChainSkip:
    assert op.source_path is not None
    if not op.amending_work_id or not op.amending_provision_href or archive is None:
        return _skip(SKIP_AMENDING_UNRESOLVED, op)
    matches = _resolve_target_nodes(tree.document, op.source_path)
    if len(matches) == 0:
        return _skip(SKIP_TARGET_ABSENT, op)
    if len(matches) > 1:
        return _skip(SKIP_AMBIGUOUS_TARGET, op)
    target = matches[0]

    replacement = _extract_replacement_payload(
        op, archive, amending_root_cache,
        base_work_year=base_work_year, base_work_number=base_work_number,
    )
    if replacement is None:
        return _skip(SKIP_PAYLOAD_NOT_EXTRACTABLE, op)

    rebased = _rebase_replacement_root(replacement.root, target.path)
    if _node_digest(rebased) == _node_digest(target) and rebased.text == target.text:
        return _skip(SKIP_REPLACE_APPLY_NO_OP, op)
    tree.swap_subtree(target.path, replacement.root, replacement.descendants)
    return _AppliedOp(family="replace", row_id=op.row_id, target_path=target.path, amendment_date_iso=op.amendment_date_iso)


def _def_term_case_fold_collision_exists(
    document: NZSourceDocument,
    parent_source_path: tuple[str, ...],
    leaf_label: str,
) -> bool:
    """Family-D probe (AGENTS §2.1 + §1.4): detect a def-term case-fold
    collision where the carried tree contains the SAME def-term under a
    different case at the same parent path.

    Returns ``True`` iff exactly one ``def-para`` node exists at
    ``parent_source_path``-rooted depth whose ``label`` (the def-term, NOT
    the address segment suffix) is a CASE-DIFFERENT variant that case-fold-
    matches ``leaf_label``. Both key cases are stripped via ``.lower()`` +
    whitespace-normalisation before the equality check; the existing
    ``def-para:Crown entity subsidiary``-vs-``def-para:crown entity
    subsidiary`` would also collide-but-differ-by-CONTENTS-heavy prefixes;
    here we restrict the collision to the SAME def-term label under case
    only (so 'subsidiary' vs 'Subsidiary' collides; 'subsidiary' vs 'Crown
    entity subsidiary' does NOT -- they are different def-terms under the
    same parent path, not the same definition).

    The collision is recogniser-exact-case-only: any WHITESPACE or PUNCT
    difference between the two label surfaces returns False (kept ambiguous
    → returns False → the insert proceeds, surfacing a genuine divergence
    chain-side for the audit to probe). Per AGENTS §1.4: relabelling by
    case-touch ALONE is forbidden; a def-term that differs in punctuation
    or whitespace is NOT a case-fold collision.

    Witness verified 2026-06-27 on the smoke corpus:

      8 Family-D witnesses on def-term case-fold collision:
        * act_public_1956_47 nz-opw-101 ('subsidiary' / 'Subsidiary')
        * act_public_1956_47 nz-opw-81   ('Government Superannuation Fund Authority' / same cap)
        * act_public_1956_47 nz-opw-82   ('Government Superannuation Fund Authority board' / same cap)
        * act_public_1956_47 nz-opw-85   ('invest' / 'Invest')
        * act_public_1956_47 nz-opw-87   ('liabilities' / 'Liabilities')
        * act_public_1956_47 nz-opw-93   ('property' / 'Property')
        * act_public_1956_47 nz-opw-94   ('rights' / 'Rights')
        * act_public_1992_122 nz-opw-55  ('electricity generator' / 'Electricity generator')

      Carried-tree start snapshot (2007-09-03 on 1956_47; 2007-09-20 on
      1992_122) holds the cap variant; amending-act directive's XML uses
      lowercase; the latter's insert fires (instead of the
      insert-already-present skip) and duplicates the def-para → the
      op-local divergence check fires local_similarity=0.0 against the
      on-or-after oracle (where only ONE case variant survives).
    """
    if not leaf_label:
        return False
    leaf_normalised = " ".join(leaf_label.lower().split())
    hits = 0
    for node in document.nodes:
        if node.kind != "def-para":
            continue
        node_parent = node.path[:-1]
        # Mirror the leading-part tolerance from ``_resolve_target_nodes`` + the
        # widened ``_oracle_target_head_is_part_wrapper`` -- the op's
        # parent_source_path may carry no leading ``part:N`` / ``part@xml_id``
        # wrapper while the carried-tree's parsed-source path DOES have such a
        # wrapper (the parser's labeled- or identity-fallback depth encoding for
        # the same logical parent). Tolerate ONE leading part-wrapper on the
        # carried-tree side (mirror of the apply-step's widening commit
        # 990e91f9 + Direction-B of the divergence resolver commit 533b4435);
        # NEVER tolerate label-tolerant fallback on the def-term itself (per
        # AGENTS §1.1).
        if node_parent != parent_source_path and not (
            len(node_parent) == len(parent_source_path) + 1
            and _oracle_target_head_is_part_wrapper(node_parent[0])
            and node_parent[1:] == parent_source_path
        ):
            continue
        node_label = node.label or ""
        if node_label == leaf_label:
            # Exact-match is the existing already-present gate's territory;
            # not a case-fold collision (caller should have routed it to the
            # already-present skip).
            continue
        node_normalised = " ".join(node_label.lower().split())
        if node_normalised == leaf_normalised and node_normalised:
            hits += 1
    return hits == 1


def _apply_insert_op(
    tree: _EvolvingTree,
    op: NZChainOp,
    archive: Any,
    amending_root_cache: dict[str, Any],
    *,
    base_work_year: str = "",
    base_work_number: str = "",
) -> _AppliedOp | NZChainSkip:
    assert op.source_path is not None
    if not op.amending_work_id or not op.amending_provision_href or archive is None:
        return _skip(SKIP_AMENDING_UNRESOLVED, op)

    new_node_source_path = op.source_path
    leaf_kind = _leaf_source_kind(new_node_source_path)
    leaf_label = _leaf_source_label(new_node_source_path)
    is_nested = len(new_node_source_path) > 1
    parent_source_path = new_node_source_path[:-1]

    # The new node must NOT already be in the carried tree (an insert ADDS it).
    if len(_resolve_target_nodes(tree.document, new_node_source_path)) > 0:
        return _skip(SKIP_INSERT_ALREADY_PRESENT, op)

    # Family-D closed 2026-06-27: def-term case-fold collision. When the leaf
    # is a ``def-para`` and the exact-match lookup missed, the carried tree MAY
    # carry the SAME def-term under a different case (the editorial
    # consolidation XML preserved the case the parser saw in that snapshot,
    # and an op amending at a later date whose XML uses a different case
    # bypasses the literal already-present gate above). Per AGENTS §1.4
    # (no silent sibling absorption by label-text or case-touch alone) +
    # §2.1 (named recovery rule + witness):
    #
    # Emit a TYPED skip receipt (distinct bucket + rule_id from the exact-
    # match insert-already-present so the case-fold absorption is auditable
    # separately) -- never a silent skip-as-already-present that would
    # otherwise absorb the variant the parser's case-preservation behaviour
    # surfaced.
    if leaf_kind == "def-para" and _def_term_case_fold_collision_exists(
        tree.document, parent_source_path, leaf_label
    ):
        return _skip(SKIP_INSERT_DEF_TERM_CASE_FOLD_COLLISION, op)

    payload = _extract_insertion_payload(
        op, leaf_kind, leaf_label, archive, amending_root_cache,
        base_work_year=base_work_year, base_work_number=base_work_number,
    )
    if payload is None:
        return _skip(SKIP_PAYLOAD_NOT_EXTRACTABLE, op)

    # Derive the anchor sibling + direction against the CARRIED tree (mirror the
    # dry-run, which derives against the before-tree).
    if is_nested:
        parent_matches = _resolve_target_nodes(tree.document, parent_source_path)
        if len(parent_matches) != 1:
            return _skip(SKIP_INSERT_ANCHOR_UNRESOLVED, op)
        resolved_parent_path = parent_matches[0].path
        sibling_nodes = _child_nodes_of_kind(tree.document, resolved_parent_path, leaf_kind)
        sibling_labels = tuple(node.label for node in sibling_nodes if node.label)
        nested_anchor = _derive_nested_insert_anchor(leaf_kind, leaf_label, sibling_labels)
        if nested_anchor is None:
            return _skip(SKIP_INSERT_ANCHOR_NOT_DERIVABLE, op)
        anchor_label, direction = nested_anchor
        anchor_source_path = (*resolved_parent_path, f"{leaf_kind}:{anchor_label}")
    elif _is_before_tree_dependent_insert_label(leaf_label):
        sibling_labels = _top_level_sibling_labels(tree.document, leaf_kind)
        top_anchor = _derive_top_level_insert_anchor(leaf_label, sibling_labels)
        if top_anchor is None:
            return _skip(SKIP_INSERT_ANCHOR_NOT_DERIVABLE, op)
        anchor_label, direction = top_anchor
        anchor_source_path = (f"{leaf_kind}:{anchor_label}",)
    else:
        top_anchor = _derive_insert_anchor(leaf_kind, leaf_label)
        if top_anchor is None:
            return _skip(SKIP_INSERT_ANCHOR_NOT_DERIVABLE, op)
        anchor_label, direction = top_anchor
        anchor_source_path = (f"{leaf_kind}:{anchor_label}",)

    anchor_matches = _resolve_target_nodes(tree.document, anchor_source_path)
    if len(anchor_matches) != 1:
        return _skip(SKIP_INSERT_ANCHOR_UNRESOLVED, op)
    anchor_node = anchor_matches[0]
    new_node_resolved_path = (*anchor_node.path[:-1], f"{leaf_kind}:{leaf_label}")
    tree.insert_node(
        anchor_node.path,
        direction,
        new_node_resolved_path,
        payload.root,
        payload.descendants,
    )
    return _AppliedOp(family="insert", row_id=op.row_id, target_path=new_node_resolved_path, amendment_date_iso=op.amendment_date_iso)


def _extract_replacement_payload(
    op: NZChainOp,
    archive: Any,
    amending_root_cache: dict[str, Any],
    *,
    base_work_year: str = "",
    base_work_number: str = "",
) -> Any:
    """Re-extract the one-to-one structural replacement payload for ``op``.

    Returns an ``NZStructuralReplacement`` or ``None`` (unresolvable amending act/
    href, or a non-clean one-to-one payload — the SAME typed blocker the dry-run
    kernel raises, here collapsed to a skip). ``base_work_year``/
    ``base_work_number`` let a schedule-indirection amendment resolve its payload
    from the schedule amendment group keyed to the base act.
    """

    assert op.source_path is not None
    leaf_kind = _leaf_source_kind(op.source_path)
    leaf_label = _leaf_source_label(op.source_path)
    amending_root = _amending_act_root(archive, op.amending_work_id, amending_root_cache)
    if amending_root is None:
        return None
    amending_node = _amending_node_by_href(amending_root, op.amending_provision_href)
    if amending_node is None:
        return None
    replacement = extract_structural_replacement(
        amending_node,
        target_leaf_kind=leaf_kind,
        target_leaf_label=leaf_label,
        base_work_year=base_work_year,
        base_work_number=base_work_number,
    )
    if isinstance(replacement, str):
        return None
    return replacement


def _extract_insertion_payload(
    op: NZChainOp,
    leaf_kind: str,
    leaf_label: str,
    archive: Any,
    amending_root_cache: dict[str, Any],
    *,
    base_work_year: str = "",
    base_work_number: str = "",
) -> Any:
    """Re-extract the single new-node insertion payload for ``op`` (or ``None``).

    ``base_work_year``/``base_work_number`` let a schedule-indirection amendment
    resolve its payload from the schedule amendment group keyed to the base act.
    """

    amending_root = _amending_act_root(archive, op.amending_work_id, amending_root_cache)
    if amending_root is None:
        return None
    amending_node = _amending_node_by_href(amending_root, op.amending_provision_href)
    if amending_node is None:
        return None
    payload = extract_structural_insertion(
        amending_node,
        inserted_leaf_kind=leaf_kind,
        inserted_leaf_label=leaf_label,
        base_work_year=base_work_year,
        base_work_number=base_work_number,
    )
    if isinstance(payload, str):
        return None
    return payload


def _skip(bucket: str, op: NZChainOp) -> NZChainSkip:
    return NZChainSkip(
        bucket=bucket,
        rule_id=_SKIP_RULE_ID[bucket],
        family=op.family,
        row_id=op.row_id,
        amendment_date_iso=op.amendment_date_iso,
        amending_work_id=op.amending_work_id,
        source_path=op.source_path or (),
    )


# --- PIT materialization + similarity-vs-oracle ---


def _node_text_index(document: NZSourceDocument) -> dict[tuple[str, ...], NZSourceNode]:
    index: dict[tuple[str, ...], NZSourceNode] = {}
    for node in document.nodes:
        index.setdefault(node.path, node)
    return index


def _stable_path(path: tuple[str, ...]) -> tuple[str, ...]:
    """Collapse positional/identity path segments to their bare kind.

    NZ's parser falls back to ``kind#ordinal`` (positional) or ``kind@xml_id``
    (identity) when a node has no stable label. Those segments churn across
    consolidations even when nothing legally changed, splitting an otherwise
    identical path. Collapsing them to ``kind`` lets a stable-track Jaccard count
    such nodes as the same logical position. (Multiple anonymous siblings under
    one parent collapse together; this slightly under-counts them, but the raw
    track keeps the exact comparison alongside.)
    """

    out: list[str] = []
    for segment in path:
        if "#" in segment:
            out.append(segment.split("#", 1)[0])
        elif "@" in segment:
            out.append(segment.split("@", 1)[0])
        else:
            out.append(segment)
    return tuple(out)


def _stable_text_index(document: NZSourceDocument) -> dict[tuple[str, ...], list[NZSourceNode]]:
    index: dict[tuple[str, ...], list[NZSourceNode]] = {}
    for node in document.nodes:
        index.setdefault(_stable_path(node.path), []).append(node)
    return index


def _node_similarity_text(node: NZSourceNode) -> str:
    # The node's own legal-text surface: heading + body + deletion marker. A
    # tombstone scores low against a substantive oracle node and high against a
    # tombstoned oracle node, which is exactly the carry-forward signal we want.
    marker = "[repealed]" if node.deletion_status else ""
    return f"{node.heading}\n{node.text}\n{marker}".strip()


def _similarity_point(
    replayed: NZSourceDocument,
    oracle: NZSourceDocument,
    version: NZArchivedVersion,
    *,
    transitions_applied: int,
    repeals_applied: int,
    repeals_skipped: int,
) -> NZChainSimilarityPoint:
    replayed_index = _node_text_index(replayed)
    oracle_index = _node_text_index(oracle)
    all_paths = sorted(replayed_index.keys() | oracle_index.keys())
    shared_paths = replayed_index.keys() & oracle_index.keys()

    union_scores: list[float] = []
    shared_scores: list[float] = []
    for path in all_paths:
        replayed_node = replayed_index.get(path)
        oracle_node = oracle_index.get(path)
        if replayed_node is None or oracle_node is None:
            # Missing/extra path: scores 0 in the FI-style combined metric.
            union_scores.append(0.0)
            continue
        score = section_similarity(
            _node_similarity_text(replayed_node), _node_similarity_text(oracle_node)
        )
        union_scores.append(score)
        shared_scores.append(score)

    combined = sum(union_scores) / len(union_scores) if union_scores else 1.0
    shared_mean = sum(shared_scores) / len(shared_scores) if shared_scores else 1.0
    union_size = len(replayed_index.keys() | oracle_index.keys())
    jaccard = len(shared_paths) / union_size if union_size else 1.0

    # Stable track: collapse positional/identity path churn so it does not split
    # otherwise-identical nodes. Per stable path, take the best per-node text
    # similarity (a stable path may bucket multiple anonymous siblings).
    replayed_stable = _stable_text_index(replayed)
    oracle_stable = _stable_text_index(oracle)
    stable_paths = sorted(replayed_stable.keys() | oracle_stable.keys())
    stable_union_scores: list[float] = []
    for path in stable_paths:
        replayed_nodes = replayed_stable.get(path)
        oracle_nodes = oracle_stable.get(path)
        if not replayed_nodes or not oracle_nodes:
            stable_union_scores.append(0.0)
            continue
        best = max(
            section_similarity(_node_similarity_text(r), _node_similarity_text(o))
            for r in replayed_nodes
            for o in oracle_nodes
        )
        stable_union_scores.append(best)
    combined_stable = (
        sum(stable_union_scores) / len(stable_union_scores) if stable_union_scores else 1.0
    )
    shared_stable = replayed_stable.keys() & oracle_stable.keys()
    union_stable = len(replayed_stable.keys() | oracle_stable.keys())
    jaccard_stable = len(shared_stable) / union_stable if union_stable else 1.0

    return NZChainSimilarityPoint(
        version_id=version.version_id,
        version_date=version.version_date,
        combined_similarity=combined,
        shared_mean_similarity=shared_mean,
        path_jaccard=jaccard,
        combined_similarity_stable=combined_stable,
        path_jaccard_stable=jaccard_stable,
        replayed_node_count=len(replayed.nodes),
        oracle_node_count=len(oracle.nodes),
        shared_path_count=len(shared_paths),
        transitions_applied_so_far=transitions_applied,
        repeals_applied_so_far=repeals_applied,
        repeals_skipped_so_far=repeals_skipped,
    )


def _oracle_tombstone_agreement(
    applied_paths: set[tuple[str, ...]],
    oracle: NZSourceDocument,
) -> tuple[int, int]:
    """Secondary per-op signal: at a path we repealed, is the oracle tombstoned?

    NZ consolidations erase the repealed body text rather than keeping an inline
    tombstone, so the oracle node is frequently *absent* at the repealed path.
    Either an absent oracle node OR a tombstoned oracle node counts as agreement
    (the repeal direction was right); a surviving substantive oracle node at a
    repealed path is a disagreement.
    """

    oracle_index = _node_text_index(oracle)
    agree = 0
    disagree = 0
    for path in applied_paths:
        oracle_node = oracle_index.get(path)
        if oracle_node is None or oracle_node.deletion_status:
            agree += 1
        else:
            disagree += 1
    return agree, disagree


# A materialized node at an applied target path agrees with the final oracle when
# its own text similarity to the oracle node at that path is at least this high.
# This is the per-op directional check for the content-producing families
# (text_replace/replace/insert); it is NOT the headline metric (the stable
# combined similarity curve is) and is never used to loosen that metric.
_PER_OP_ORACLE_AGREEMENT_THRESHOLD = 0.9


# Below this op-local similarity, a content-producing op's produced node is judged
# to CONTRADICT the oracle node at its target path (a wrong op corrupting the
# tree). Set conservatively so only clear divergences fire — the goal is to catch
# composition/ordering bugs and wrong payloads, not to penalize the oracle's
# co-occurring window churn.
_OP_LOCAL_DIVERGENCE_THRESHOLD = 0.5


def _op_local_divergence(
    materialized: NZSourceDocument,
    applied_op: "_AppliedOp",
    versions_asc: tuple[NZArchivedVersion, ...],
    archive: Any,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> "NZChainDivergence | None":
    """Op-local honesty check for a content-producing op (or ``None`` if it agrees).

    Compares the node the op just produced at ``applied_op.target_path`` against
    the oracle node at that path in the earliest archived version on-or-after the
    op's effective date (the version that should reflect the change). A local
    similarity below :data:`_OP_LOCAL_DIVERGENCE_THRESHOLD` — including an oracle
    that lacks the node entirely — is a divergence.
    """

    oracle_version = _earliest_version_on_or_after(versions_asc, applied_op.amendment_date_iso)
    if oracle_version is None:
        return None
    oracle_doc = _parse_archived_version(archive, oracle_version, parsed_cache)
    if oracle_doc is None:
        return None
    materialized_node = _node_text_index(materialized).get(applied_op.target_path)
    if materialized_node is None:
        return None  # the produced node was overwritten by a later op; not this op
    oracle_node = _resolve_oracle_node_for_target(oracle_doc, applied_op.target_path)
    local_similarity = (
        section_similarity(
            _node_similarity_text(materialized_node), _node_similarity_text(oracle_node)
        )
        if oracle_node is not None
        else 0.0
    )
    if local_similarity >= _OP_LOCAL_DIVERGENCE_THRESHOLD:
        return None
    return NZChainDivergence(
        family=applied_op.family,
        row_id=applied_op.row_id,
        target_path=applied_op.target_path,
        oracle_version_id=oracle_version.version_id,
        oracle_version_date=oracle_version.version_date,
        local_similarity=local_similarity,
    )


def _resolve_oracle_node_for_target(
    oracle_doc: NZSourceDocument,
    target_path: tuple[str, ...],
) -> NZSourceNode | None:
    """Resolve the oracle node at ``target_path`` with leading-part-drop tolerance.

    The op-local divergence check keys on ``applied_op.target_path`` which lives
    in the carried tree's path encoding (the parsed-shape of the EARLIEST archived
    snapshot). NZ's parser falls back to ``part@DLM_xml_id`` (unlabeled-`<part>`
    identity) when a `<part>` element lacks a parseable `<label>` -- observed on
    199 nodes in the earliest act_public_1981_23 snapshot (the chain's start
    state). The oracle (later archived snapshot) can carry the same prov:N under
    one of TWO shapes after editorial consolidation re-standardised the XML:

      (1) ``prov:N``            -- no `<part>` wrapper at all (oracle dropped the
                                    wrapper entirely because the `<part>` element
                                    no longer appears in the post-restoration XML).
      (2) ``part@DLM_X/prov:N`` -- unlabeled identity fallback (same carried-tree
                                    shape; the wrapper persisted across snapshots;
                                    covered by the exact-match fast path).

    The literal ``target_path == ('part@DLM44815', 'prov:22')`` lookup returns
    None when the oracle carries Shape (1), producing a false
    ``local_similarity=0.0`` divergence encoding-mismatch artefact rather than
    an honest materialized-vs-oracle disagreement. Witness cluster verified
    2026-06-27 on act_public_1981_23 chain-replay: 45 op-local divergences,
    100% at target_path[0]='part@DLM_*' segments, 100% local_similarity=0.0,
    100% resolve to Shape (1) under the oracle probe (prov:N at top level with
    no part wrapper) -- the entire 45-row cluster is carried-tree-path-shape
    -vs-oracle artefact.

    The widening accepts Direction B (drop-wrapper): when the carried-tree's
    leading segment is a part wrapper AND the literal lookup misses, accept
    an oracle node whose path equals ``target_path[1:]`` (i.e., the wrapper
    was dropped entirely on the oracle side). Single-match enforcement stays;
    an empty or ambiguous result keeps returning None -- the divergence
    check then correctly fires ``local_similarity=0.0`` for a genuinely-absent
    oracle target, which is the honest signal.

    Narrowness (per AGENTS §1.1 no silent target hijacking):

    * The widening is restricted to the case where the carried-tree's head is
      a ``part:N`` or ``part@xml_id`` segment (the parser's known-but-unlabeled
      ``<part>`` wrapper shapes). Other leading-segment shapes are NOT widened
      -- only the part-wrapper-shape-churn family.
    * The widening accepts only ONE direction (drop-the-wrapper), NOT label-
      tolerant fallback: prov:N labels are exact-matched; only the part
      wrapper's presence-vs-absence is tolerated.
    * Same-length-different-suffix variants (e.g. ``part:N`` vs ``part@X``
      would match each other under label-tolerant) are NOT accepted -- those
      are the parser's identity-vs-label choice and accepting them would
      silently cross-snapshot-collapse two distinct part identities.
    """
    oracle_index = _node_text_index(oracle_doc)
    # Fast path: exact-match (the common, no-part@-shape case).
    exact = oracle_index.get(target_path)
    if exact is not None:
        return exact
    if not target_path:
        return None
    head = target_path[0]
    if not _oracle_target_head_is_part_wrapper(head):
        # No leading-part-wrapper on the carried tree -- no path-shape churn to
        # tolerate; the oracle honestly lacks the target.
        return None
    # Direction B (drop-wrapper): carried-tree's leading part-wrapper is absent
    # from the oracle; accept an oracle node whose path equals target_path[1:].
    tail = target_path[1:]
    candidates = [node for node in oracle_doc.nodes if node.path == tail]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _oracle_target_head_is_part_wrapper(segment: str) -> bool:
    """The same ``part:N`` / ``part@xml_id`` widened predicate the apply-step
    resolver uses (kept here as a local mirror so the divergence lane does not
    import dry_run.py's ``_is_leading_part_segment`` and create a new dependency
    cycle; the predicate's behaviour is pinned by the same paired synthetic
    test landed 2026-06-24)."""
    if not segment:
        return False
    if segment.split(":", 1)[0] == "part":
        return True
    return segment.startswith("part@")


def _earliest_version_on_or_after(
    versions_asc: tuple[NZArchivedVersion, ...],
    date_iso: str,
) -> NZArchivedVersion | None:
    if not date_iso:
        return None
    for version in versions_asc:
        if version.version_date >= date_iso:
            return version
    return None


def _oracle_content_agreement(
    materialized: NZSourceDocument,
    applied_paths: set[tuple[str, ...]],
    oracle: NZSourceDocument,
) -> tuple[int, int]:
    """Per-op signal for content-producing families: does the produced node match?

    At each applied target path, compare the materialized node's own text against
    the final oracle node at that path. Agreement requires the oracle to carry a
    node whose text similarity to the materialized node is at least
    :data:`_PER_OP_ORACLE_AGREEMENT_THRESHOLD`; an absent oracle node (the produced
    node has no oracle counterpart) or a low-similarity node is a disagreement.
    Unlike the repeal direction (where absence is agreement), a content-producing
    op that yields a node the oracle lacks is a divergence, not a success.
    """

    materialized_index = _node_text_index(materialized)
    oracle_index = _node_text_index(oracle)
    agree = 0
    disagree = 0
    for path in applied_paths:
        materialized_node = materialized_index.get(path)
        oracle_node = oracle_index.get(path)
        if materialized_node is None or oracle_node is None:
            disagree += 1
            continue
        score = section_similarity(
            _node_similarity_text(materialized_node), _node_similarity_text(oracle_node)
        )
        if score >= _PER_OP_ORACLE_AGREEMENT_THRESHOLD:
            agree += 1
        else:
            disagree += 1
    return agree, disagree


# --- Driver ---


def resolve_families(spec: str | frozenset[str] | None) -> frozenset[str]:
    """Resolve a ``--families`` spec to a validated set of family names.

    ``None`` / ``"all"`` -> every family. ``"repeal"`` (or any single family
    name) -> that family only. A comma-separated list selects several. An unknown
    name fails loud rather than silently selecting nothing.
    """

    if spec is None:
        return _ALL_FAMILIES
    if isinstance(spec, frozenset):
        return spec
    spec = spec.strip().lower()
    if spec in ("", "all"):
        return _ALL_FAMILIES
    names = frozenset(part.strip() for part in spec.split(",") if part.strip())
    unknown = names - _ALL_FAMILIES
    if unknown:
        raise ValueError(
            f"unknown chain-replay family(ies) {sorted(unknown)}; "
            f"valid families are {sorted(_ALL_FAMILIES)} or 'all'"
        )
    return names


def build_archived_work_chain_replay(
    db_path: Path,
    work_id: str,
    *,
    families: str | frozenset[str] | None = None,
) -> NZChainReplayReport:
    resolved = resolve_families(families)
    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    # The structural families read candidate witnesses from the operation surface;
    # build it only when a structural family is requested (it parses the amending
    # acts lazily inside the kernel).
    surface = None
    if resolved & {"replace", "insert"}:
        from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

        surface = build_archived_work_operation_surface(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_chain_replay(
            archive,
            work_id=work_id,
            preflight=preflight,
            surface=surface,
            families=resolved,
        )
    finally:
        archive.close()


def build_chain_replay(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    surface: Any | None = None,
    families: str | frozenset[str] | None = None,
) -> NZChainReplayReport:
    resolved = resolve_families(families)
    transitions = build_nz_chain(preflight, surface, families=resolved)
    family_label = "+".join(family for family in CHAIN_FAMILY_ORDER if family in resolved)

    versions = archived_xml_versions_for_work(archive, work_id)
    # ``archived_xml_versions_for_work`` is newest-first; the chain runs oldest to
    # newest, so reverse into ascending order.
    versions_asc = tuple(reversed(versions))

    enumerated_per_family = _count_enumerated_per_family(transitions)
    empty_report = NZChainReplayReport(
        work_id=work_id,
        operation_family=family_label,
        base_version_id="",
        base_version_date="",
        n_archived_versions=len(versions_asc),
        transitions=transitions,
        similarity_curve=(),
        repeals_applied=0,
        repeals_skipped=0,
        oracle_tombstone_agreements=0,
        oracle_tombstone_disagreements=0,
        skips=(),
        per_family_stats=tuple(
            NZChainPerFamilyStat(
                family=family,
                enumerated=enumerated_per_family.get(family, 0),
                applied=0,
                skipped=0,
                oracle_agreements=0,
                oracle_disagreements=0,
            )
            for family in CHAIN_FAMILY_ORDER
            if family in resolved
        ),
        families_requested=tuple(f for f in CHAIN_FAMILY_ORDER if f in resolved),
    )
    if not versions_asc:
        return empty_report

    parsed_cache: dict[str, NZSourceDocument | None] = {}
    amending_root_cache: dict[str, Any] = {}
    base_work_year, base_work_number = _base_work_year_number(work_id)
    base_version = versions_asc[0]
    base_doc = _parse_archived_version(archive, base_version, parsed_cache)
    if base_doc is None:
        return empty_report

    latest_version_date = versions_asc[-1].version_date

    tree = _EvolvingTree(document=base_doc)
    curve: list[NZChainSimilarityPoint] = []
    all_skips: list[NZChainSkip] = []
    divergences: list[NZChainDivergence] = []
    # Per-family applied target paths (for the per-family oracle agreement check).
    applied_paths_by_family: dict[str, set[tuple[str, ...]]] = {
        family: set() for family in CHAIN_FAMILY_ORDER
    }
    applied_count_by_family: dict[str, int] = {family: 0 for family in CHAIN_FAMILY_ORDER}
    ops_applied = 0
    transitions_applied = 0

    def _run_transition(transition: NZChainTransition) -> list[_AppliedOp]:
        nonlocal ops_applied, transitions_applied
        applied, skips, applied_ops = _apply_transition(
            tree,
            transition,
            latest_version_date=latest_version_date,
            archive=archive,
            amending_root_cache=amending_root_cache,
            base_work_year=base_work_year,
            base_work_number=base_work_number,
        )
        ops_applied += applied
        all_skips.extend(skips)
        for applied_op in applied_ops:
            applied_paths_by_family[applied_op.family].add(applied_op.target_path)
            applied_count_by_family[applied_op.family] += 1
            # Op-local honesty guard for content-producing families: the node the
            # op just produced must MATCH the oracle node at its target path in the
            # version that should reflect the change. A low local similarity is a
            # wrong op (mis-extracted payload / wrong target / bad anchor) — the
            # highest-value finding. Repeal is excluded here: its direction is
            # checked by the tombstone-agreement signal (absence is agreement), and
            # a low local text similarity for a correct repeal is expected.
            if applied_op.family != "repeal":
                divergence = _op_local_divergence(
                    tree.document, applied_op, versions_asc, archive, parsed_cache
                )
                if divergence is not None:
                    divergences.append(divergence)
        transitions_applied += 1
        return applied_ops

    transition_cursor = 0
    for version in versions_asc:
        # Apply every transition effective on-or-before this archived version's
        # date that has not yet been applied.
        while (
            transition_cursor < len(transitions)
            and transitions[transition_cursor].amendment_date_iso <= version.version_date
        ):
            _run_transition(transitions[transition_cursor])
            transition_cursor += 1

        oracle_doc = _parse_archived_version(archive, version, parsed_cache)
        if oracle_doc is None:
            continue
        curve.append(
            _similarity_point(
                tree.document,
                oracle_doc,
                version,
                transitions_applied=transitions_applied,
                repeals_applied=ops_applied,
                repeals_skipped=len(all_skips),
            )
        )

    # Apply any remaining transitions whose date is after the latest archived
    # version (these all land in the future skip bucket via _apply_transition).
    while transition_cursor < len(transitions):
        _run_transition(transitions[transition_cursor])
        transition_cursor += 1

    final_oracle = _parse_archived_version(archive, versions_asc[-1], parsed_cache)
    repeal_agree = repeal_disagree = 0
    per_family_oracle: dict[str, tuple[int, int]] = {}
    if final_oracle is not None:
        repeal_agree, repeal_disagree = _oracle_tombstone_agreement(
            applied_paths_by_family["repeal"], final_oracle
        )
        per_family_oracle["repeal"] = (repeal_agree, repeal_disagree)
        for family in ("text_replace", "replace", "insert"):
            per_family_oracle[family] = _oracle_content_agreement(
                tree.document, applied_paths_by_family[family], final_oracle
            )

    per_family_stats = tuple(
        NZChainPerFamilyStat(
            family=family,
            enumerated=enumerated_per_family.get(family, 0),
            applied=applied_count_by_family[family],
            skipped=sum(1 for skip in all_skips if skip.family == family),
            oracle_agreements=per_family_oracle.get(family, (0, 0))[0],
            oracle_disagreements=per_family_oracle.get(family, (0, 0))[1],
        )
        for family in CHAIN_FAMILY_ORDER
        if family in resolved
    )

    return NZChainReplayReport(
        work_id=work_id,
        operation_family=family_label,
        base_version_id=base_version.version_id,
        base_version_date=base_version.version_date,
        n_archived_versions=len(versions_asc),
        transitions=transitions,
        similarity_curve=tuple(curve),
        repeals_applied=ops_applied,
        repeals_skipped=len(all_skips),
        oracle_tombstone_agreements=repeal_agree,
        oracle_tombstone_disagreements=repeal_disagree,
        skips=tuple(all_skips),
        per_family_stats=per_family_stats,
        divergences=tuple(divergences),
        families_requested=tuple(f for f in CHAIN_FAMILY_ORDER if f in resolved),
    )


def _count_enumerated_per_family(transitions: tuple[NZChainTransition, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for transition in transitions:
        for op in transition.ops:
            counts[op.family] = counts.get(op.family, 0) + 1
    return counts


def main(args: Any) -> None:
    families = getattr(args, "families", None)
    report = build_archived_work_chain_replay(
        Path(args.db), args.work_id, families=families
    )
    if args.json:
        print(
            json.dumps(
                report.to_jsonable(summary_only=getattr(args, "summary_only", False)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    summary = report.summary()
    print(
        f"work_id={summary['work_id']} families={summary['operation_family']} "
        f"replay_claims={summary['replay_claims']} "
        f"(experimental dry-run chain replay, partial coverage)"
    )
    print(
        f"base={summary['base_version_id']} "
        f"chain: n_versions={summary['n_archived_versions']} "
        f"n_transitions={summary['n_transitions']} "
        f"total_ops={summary['total_repeal_ops']}"
    )
    print(
        f"ops_applied={summary['repeals_applied']} "
        f"ops_skipped={summary['repeals_skipped']} "
        f"skip_buckets={summary['skip_bucket_counts']}"
    )
    print("per_family (enumerated / applied / skipped / oracle_agree):")
    for stat in report.per_family_stats:
        oracle_total = stat.oracle_agreements + stat.oracle_disagreements
        print(
            f"  {stat.family:<13} enumerated={stat.enumerated} applied={stat.applied} "
            f"skipped={stat.skipped} "
            f"oracle_agree={stat.oracle_agreements}/{oracle_total}"
        )
    print(
        f"final_similarity raw:    combined={summary['final_combined_similarity']} "
        f"shared_mean={summary['final_shared_mean_similarity']} "
        f"path_jaccard={summary['final_path_jaccard']}"
    )
    print(
        f"final_similarity stable: combined={summary['final_combined_similarity_stable']} "
        f"path_jaccard={summary['final_path_jaccard_stable']}  "
        f"(positional/id path churn collapsed)"
    )
    if report.divergences:
        print(
            f"DIVERGENCES ({len(report.divergences)}): a content-producing op yielded "
            f"a node the oracle contradicts (wrong payload/target/anchor or "
            f"composition/ordering bug):"
        )
        for div in report.divergences:
            print(
                f"  {div.family}:{div.row_id}  "
                f"target={'/'.join(div.target_path)}  "
                f"oracle@{div.oracle_version_date}  "
                f"local_similarity={div.local_similarity:.4f}"
            )
    print(
        "similarity_curve (version_date -> raw combined / shared_mean / jaccard | "
        "stable combined / jaccard | applied/skipped):"
    )
    for point in report.similarity_curve:
        print(
            f"  {point.version_date}  "
            f"raw[c={point.combined_similarity:.4f} sm={point.shared_mean_similarity:.4f} "
            f"j={point.path_jaccard:.4f}]  "
            f"stable[c={point.combined_similarity_stable:.4f} j={point.path_jaccard_stable:.4f}]  "
            f"| applied={point.repeals_applied_so_far} skipped={point.repeals_skipped_so_far}"
        )
