"""Experimental amendment-chain replay for New Zealand (repeal-only).

This is the first NZ end-to-end replay. Unlike the per-operation dry-run
(:mod:`lawvm.new_zealand.dry_run`) and the per-window dry-run-oracle comparison
(:mod:`lawvm.new_zealand.dry_run_oracle`) — both of which reset to a freshly
parsed archived *before* document for every window — this surface carries a
**single evolving tree** across the whole amendment chain:

1. Enumerate a base work's authorized repeal witnesses, grouped by effective
   amendment date into ordered transitions (the NZ analogue of Norway's
   ``entries_for_base``; built locally from the preflight's per-op repeal rows).
2. Start from the **earliest** archived consolidated version of the work. (NZ
   archives point-in-time XML from 2007-09-03 onward, so pre-2007 amendments are
   already baked into that base — they are not re-applied here; an op whose target
   is already absent/tombstoned in the evolving tree is a typed skip, never a
   silent drop.)
3. Apply each transition's preflight-authorized repeals to the **current**
   evolving tree (resolve the exact target in the carried tree, tombstone it with
   the existing :func:`lawvm.new_zealand.dry_run._tombstone_node` kernel) and
   carry the mutated tree forward — errors and skips accumulate down the chain.
4. At each archived version date, materialize the evolving tree and compare it to
   the archived consolidated **oracle** with the core continuous metric
   :func:`lawvm.core.evidence_support.section_similarity` (so partial op coverage
   produces a *similarity curve*, not a useless binary pass/fail).

This is an **experimental dry-run chain replay with partial coverage**. It never
authorizes actual replay (``replay_claims`` stays ``False``), never mutates the
archive, and never turns the oracle into source truth. It reports similarity, not
a verdict. Every non-applied operation is a typed, visible skip residual: replayed
nodes + typed skips = the full repeal-op census.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.evidence_support import section_similarity
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dry_run import (
    _occupancy,
    _parse_archived_version,
    _replayable_repeal_rows,
    _resolve_target_nodes,
    _source_path_for_address,
    _tombstone_node,
)
from lawvm.new_zealand.effect_candidates import (
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode
from lawvm.new_zealand.version_diff import (
    NZArchivedVersion,
    archived_xml_versions_for_work,
)

# Honesty: this surface is an experimental partial-coverage chain replay. It does
# NOT authorize canonical effect replay anywhere.
NZ_CHAIN_REPLAY_TRUTH_CLAIM = (
    "experimental_dry_run_chain_replay_partial_repeal_coverage_not_canonical"
)
NZ_CHAIN_REPLAY_REPLAY_CLAIMS = False

# Typed skip buckets (mirror Norway's NOReplayResult typed residual lists — never
# a silent drop). One residual per skipped op.
SKIP_UNEXTRACTABLE = "amendment_skipped_unextractable"
SKIP_UNRESOLVED_TARGET = "amendment_skipped_unresolved_target"
SKIP_TARGET_ABSENT = "amendment_skipped_target_absent"
SKIP_AMBIGUOUS_TARGET = "amendment_skipped_ambiguous_target"
SKIP_ALREADY_TOMBSTONED = "amendment_skipped_already_tombstoned"
SKIP_FUTURE = "amendment_skipped_future"

_SKIP_RULE_ID: dict[str, str] = {
    SKIP_UNEXTRACTABLE: "nz_chain_replay_op_unextractable_no_source_path",
    SKIP_UNRESOLVED_TARGET: "nz_chain_replay_op_target_resolution_not_exact",
    SKIP_TARGET_ABSENT: "nz_chain_replay_target_absent_in_evolving_tree",
    SKIP_AMBIGUOUS_TARGET: "nz_chain_replay_target_ambiguous_in_evolving_tree",
    SKIP_ALREADY_TOMBSTONED: "nz_chain_replay_target_already_tombstoned_in_evolving_tree",
    SKIP_FUTURE: "nz_chain_replay_effective_date_after_latest_archived_version",
}


@dataclass(frozen=True)
class NZChainSkip:
    """One typed, non-silent skip of an authorized repeal op."""

    bucket: str
    rule_id: str
    row_id: str
    amendment_date_iso: str
    amending_work_id: str
    source_path: tuple[str, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "rule_id": self.rule_id,
            "row_id": self.row_id,
            "amendment_date_iso": self.amendment_date_iso,
            "amending_work_id": self.amending_work_id,
            "source_path": list(self.source_path),
        }


@dataclass(frozen=True)
class NZChainRepealOp:
    """One authorized repeal witness in the chain (an enumerated transition op)."""

    row_id: str
    amendment_date_iso: str
    amending_work_id: str
    source_path: tuple[str, ...] | None
    target_resolution_status: str


@dataclass(frozen=True)
class NZChainTransition:
    """All authorized repeal ops effective on one amendment date, date-ordered."""

    amendment_date_iso: str
    ops: tuple[NZChainRepealOp, ...]

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
        }

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "experimental_dry_run_chain_replay",
            "truth_claim": self.truth_claim,
            "replay_claims": self.replay_claims,
            "summary": self.summary(),
            "similarity_curve": [point.to_jsonable() for point in self.similarity_curve],
        }
        if not summary_only:
            payload["transitions"] = [
                {
                    "amendment_date_iso": transition.amendment_date_iso,
                    "n_ops": transition.n_ops,
                    "ops": [
                        {
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


def build_nz_repeal_chain(
    preflight: NZEffectCandidatePreflightReport,
) -> tuple[NZChainTransition, ...]:
    """Enumerate a base work's authorized repeal ops as date-ordered transitions.

    Per-op authorization comes from the preflight's replayable repeal rows
    (:func:`_replayable_repeal_rows`) — the SAME set the per-op dry-run consumes,
    but NOT gated on the whole-work ``ready_for_dry_run_replay`` readiness (a
    partial-coverage chain replay must enumerate the repeals it CAN authorize per
    op, not refuse the whole work). Ops are bucketed by ``amendment_date_iso`` and
    emitted in ISO-date order; within a date, ordered by ``(amending_work_id,
    row_id)`` for determinism.
    """

    by_date: dict[str, list[NZChainRepealOp]] = {}
    for row in _replayable_repeal_rows(preflight):
        amendment_date_iso = row.amendment_date_iso
        if not amendment_date_iso:
            # An op with no effective date cannot be placed in the chain; the
            # preflight already proved it replayable, so this is a real gap, not a
            # silent drop — surface it as a degenerate transition keyed by the
            # empty date so the census still counts it.
            amendment_date_iso = ""
        operation = row.operation
        source_path = _source_path_for_address(operation) if operation is not None else None
        op = NZChainRepealOp(
            row_id=row.row_id,
            amendment_date_iso=amendment_date_iso,
            amending_work_id=row.amending_work_id,
            source_path=source_path,
            target_resolution_status=row.latest_oracle_target_resolution_status or "",
        )
        by_date.setdefault(amendment_date_iso, []).append(op)

    transitions: list[NZChainTransition] = []
    for amendment_date_iso in sorted(by_date):
        ops = sorted(
            by_date[amendment_date_iso],
            key=lambda op: (op.amending_work_id, op.row_id),
        )
        transitions.append(
            NZChainTransition(amendment_date_iso=amendment_date_iso, ops=tuple(ops))
        )
    return tuple(transitions)


# --- Sequential apply on one evolving tree ---


@dataclass
class _EvolvingTree:
    """A mutable wrapper over the carried document for in-chain repeals."""

    document: NZSourceDocument

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
        self.document = NZSourceDocument(
            xml_locator=self.document.xml_locator,
            version_id=self.document.version_id,
            metadata=self.document.metadata,
            nodes=tuple(new_nodes),
            document_history=self.document.document_history,
        )


def _apply_transition(
    tree: _EvolvingTree,
    transition: NZChainTransition,
    *,
    latest_version_date: str,
) -> tuple[int, list[NZChainSkip], list[tuple[str, ...]]]:
    """Apply one transition's authorized repeals to the evolving tree.

    Returns ``(applied_count, skips, applied_paths)``. Every op that cannot be
    applied is a typed skip, never a silent drop.
    """

    applied = 0
    skips: list[NZChainSkip] = []
    applied_paths: list[tuple[str, ...]] = []

    for op in transition.ops:
        if op.amendment_date_iso and latest_version_date and op.amendment_date_iso > latest_version_date:
            skips.append(_skip(SKIP_FUTURE, op))
            continue
        if op.source_path is None:
            skips.append(_skip(SKIP_UNEXTRACTABLE, op))
            continue
        # Defence in depth mirrors the dry-run kernel: only exact targets mutate.
        if op.target_resolution_status and op.target_resolution_status != "exact_source_path":
            skips.append(_skip(SKIP_UNRESOLVED_TARGET, op))
            continue
        matches = _resolve_target_nodes(tree.document, op.source_path)
        if len(matches) == 0:
            # Target not in the carried tree: either already baked into the
            # pre-2007 base, or removed by an earlier transition, or carry-forward
            # drift. Honest typed skip.
            skips.append(_skip(SKIP_TARGET_ABSENT, op))
            continue
        if len(matches) > 1:
            skips.append(_skip(SKIP_AMBIGUOUS_TARGET, op))
            continue
        target = matches[0]
        if _occupancy(target) != "substantive":
            skips.append(_skip(SKIP_ALREADY_TOMBSTONED, op))
            continue
        tree.tombstone(target.path)
        applied += 1
        applied_paths.append(target.path)

    return applied, skips, applied_paths


def _skip(bucket: str, op: NZChainRepealOp) -> NZChainSkip:
    return NZChainSkip(
        bucket=bucket,
        rule_id=_SKIP_RULE_ID[bucket],
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


# --- Driver ---


def build_archived_work_chain_replay(db_path: Path, work_id: str) -> NZChainReplayReport:
    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_chain_replay(archive, work_id=work_id, preflight=preflight)
    finally:
        archive.close()


def build_chain_replay(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
) -> NZChainReplayReport:
    transitions = build_nz_repeal_chain(preflight)

    versions = archived_xml_versions_for_work(archive, work_id)
    # ``archived_xml_versions_for_work`` is newest-first; the chain runs oldest to
    # newest, so reverse into ascending order.
    versions_asc = tuple(reversed(versions))

    empty_report = NZChainReplayReport(
        work_id=work_id,
        operation_family="repeal",
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
    )
    if not versions_asc:
        return empty_report

    parsed_cache: dict[str, NZSourceDocument | None] = {}
    base_version = versions_asc[0]
    base_doc = _parse_archived_version(archive, base_version, parsed_cache)
    if base_doc is None:
        return empty_report

    latest_version_date = versions_asc[-1].version_date

    tree = _EvolvingTree(document=base_doc)
    curve: list[NZChainSimilarityPoint] = []
    all_skips: list[NZChainSkip] = []
    applied_paths_all: set[tuple[str, ...]] = set()
    repeals_applied = 0
    transitions_applied = 0

    # Map each transition to the earliest archived version whose version_date is
    # on-or-after the amendment date; that version is the oracle that should
    # reflect the transition's effect. We sample the curve at every archived
    # version, applying all transitions whose date falls at-or-before that
    # version's date before scoring it.
    transition_cursor = 0
    for version in versions_asc:
        # Apply every transition effective on-or-before this archived version's
        # date that has not yet been applied.
        while (
            transition_cursor < len(transitions)
            and transitions[transition_cursor].amendment_date_iso <= version.version_date
        ):
            transition = transitions[transition_cursor]
            applied, skips, applied_paths = _apply_transition(
                tree, transition, latest_version_date=latest_version_date
            )
            repeals_applied += applied
            all_skips.extend(skips)
            applied_paths_all.update(applied_paths)
            transitions_applied += 1
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
                repeals_applied=repeals_applied,
                repeals_skipped=len(all_skips),
            )
        )

    # Apply any remaining transitions whose date is after the latest archived
    # version (these all land in the future skip bucket via _apply_transition).
    while transition_cursor < len(transitions):
        transition = transitions[transition_cursor]
        applied, skips, applied_paths = _apply_transition(
            tree, transition, latest_version_date=latest_version_date
        )
        repeals_applied += applied
        all_skips.extend(skips)
        applied_paths_all.update(applied_paths)
        transitions_applied += 1
        transition_cursor += 1

    final_oracle = _parse_archived_version(archive, versions_asc[-1], parsed_cache)
    agree = disagree = 0
    if final_oracle is not None:
        agree, disagree = _oracle_tombstone_agreement(applied_paths_all, final_oracle)

    return NZChainReplayReport(
        work_id=work_id,
        operation_family="repeal",
        base_version_id=base_version.version_id,
        base_version_date=base_version.version_date,
        n_archived_versions=len(versions_asc),
        transitions=transitions,
        similarity_curve=tuple(curve),
        repeals_applied=repeals_applied,
        repeals_skipped=len(all_skips),
        oracle_tombstone_agreements=agree,
        oracle_tombstone_disagreements=disagree,
        skips=tuple(all_skips),
    )


def main(args: Any) -> None:
    report = build_archived_work_chain_replay(Path(args.db), args.work_id)
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
        f"work_id={summary['work_id']} family={summary['operation_family']} "
        f"replay_claims={summary['replay_claims']} "
        f"(experimental dry-run chain replay, partial repeal coverage)"
    )
    print(
        f"base={summary['base_version_id']} "
        f"chain: n_versions={summary['n_archived_versions']} "
        f"n_transitions={summary['n_transitions']} "
        f"total_repeal_ops={summary['total_repeal_ops']}"
    )
    print(
        f"repeals_applied={summary['repeals_applied']} "
        f"repeals_skipped={summary['repeals_skipped']} "
        f"skip_buckets={summary['skip_bucket_counts']}"
    )
    print(
        f"oracle_tombstone_agreement={summary['oracle_tombstone_agreements']}/"
        f"{summary['oracle_tombstone_agreements'] + summary['oracle_tombstone_disagreements']}"
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
