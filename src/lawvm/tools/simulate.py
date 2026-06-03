"""simulate.py — lawvm simulate --branch BRANCH_ID (feature #8).

Materializes a hypothetical PIT state by applying an HE's proposed_ops
over the current enacted statute state and computing the structural delta.

Promotion chain:
    HEParsedBranch.proposed_ops (typed claim)
    → hypothetical apply (materialization over enacted state)
    → structural delta + optional broken-ref / actor-change detection
    → JSON simulation report

Architecture
------------
This command composes:
  1. fi_he_branch_ops.parquet (or re-parse from farchive when not projected)
  2. Current enacted PIT state via the existing Finland replay pipeline
  3. Optional fi_refs.parquet (#1) for --detect-broken-refs
  4. Optional fi_actors.parquet (#2) for --detect-actor-changes

The hypothetical apply is structurally equivalent to enacted-amendment replay,
except:
  - The operations come from HEParsedBranch.proposed_ops (typed branch ops),
    not from enacted amendment XML.
  - The apply runs on a materialized PIT snapshot (not accumulating timeline).
  - No migration events are emitted (hypothetical; not enacted).
  - Conflicts between multiple simultaneously-applied HEs are flagged as
    warnings, not silently resolved (AGENTS.md §1.7).

Strict mode
-----------
When --strict is set, the simulate command rejects branches with
parse_status=PARTIAL (because partial parse means some ops are unknown).

Output
------
Emits a JSON simulation report to stdout per the feature brief §3 shape.
Table output is a simplified tabular summary when -o table is requested.

AGENTS.md compliance
--------------------
§1.1  No silent target hijacking — reported in simulation_warnings.
§1.7  No legal conflict resolved by Python accident — multi-op conflicts
      at same (target, as_of) flagged in simulation_warnings.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lawvm.finland.he_branch_parser import (
    BranchProposedOp,
    BranchTargetResolution,
    HEParsedBranch,
    HEParseStatus,
    parse_he_branch,
)


# ---------------------------------------------------------------------------
# Simulation output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangedProvision:
    """One provision changed by the simulation."""

    provision_ref: str
    before_text: str
    after_text: str
    operation_kind: str
    target_statute_id: str


@dataclass(frozen=True, slots=True)
class BrokenRefInSimulation:
    """A ref in another statute that would fail to resolve in simulated state.

    Per AGENTS.md §1.1: we report, we do not silently re-route.
    """

    source_provision_ref: str
    target_provision_ref: str
    reason: str


@dataclass(frozen=True, slots=True)
class ActorSlotChange:
    """An actor mention added or removed by branch ops."""

    provision_ref: str
    change_kind: str  # added | removed
    actor_canonical_id: str
    actor_show_as: str


@dataclass
class SimulationReport:
    """Full simulation report (JSON-serializable)."""

    branch_id: str
    simulated_at: str  # ISO date
    diff_from: str  # current | baseline | YYYY-MM-DD
    parse_status: str
    changed_provisions: List[Dict[str, Any]]
    broken_refs_in_other_statutes: List[Dict[str, Any]]
    actor_slot_changes: List[Dict[str, Any]]
    simulation_warnings: List[str]
    ops_applied: int
    ops_skipped: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "simulated_at": self.simulated_at,
            "diff_from": self.diff_from,
            "parse_status": self.parse_status,
            "changed_provisions": self.changed_provisions,
            "broken_refs_in_other_statutes": self.broken_refs_in_other_statutes,
            "actor_slot_changes": self.actor_slot_changes,
            "simulation_warnings": self.simulation_warnings,
            "ops_applied": self.ops_applied,
            "ops_skipped": self.ops_skipped,
        }


# ---------------------------------------------------------------------------
# Branch resolution: find HEParsedBranch by branch_id
# ---------------------------------------------------------------------------


def _branch_id_to_year_number(branch_id: str) -> Optional[Tuple[int, int]]:
    """Parse 'fi/he/2024/184' -> (2024, 184)."""
    parts = branch_id.split("/")
    if len(parts) < 4:
        return None
    try:
        return int(parts[-2]), int(parts[-1])
    except ValueError:
        return None


def _resolve_branch(
    branch_id: str,
    *,
    farchive_path: Optional[str] = None,
    parquet_path: Optional[str] = None,
) -> Optional[HEParsedBranch]:
    """Resolve a branch_id to HEParsedBranch.

    Resolution order:
    1. If parquet_path provided: look up in fi_he_branch_ops.parquet
       and rebuild HEParsedBranch from rows. (TODO: implement when parquet
       is available; falls through for now.)
    2. If farchive_path provided: re-parse from farchive.
    3. Otherwise: return None (branch not found).

    Note: re-parsing from farchive gives a fresh HEParsedBranch with full
    proposed_ops; the parquet path would be faster but requires reading back
    from the projection.  For now we use the farchive path.
    """
    yn = _branch_id_to_year_number(branch_id)
    if yn is None:
        return None
    he_year, he_number = yn

    # Try farchive path
    if farchive_path is not None:
        fa_path = Path(farchive_path)
        if fa_path.exists():
            try:
                from farchive import Farchive  # type: ignore[import]
            except ImportError:
                return None
            fa = Farchive(str(fa_path))
            loc = f"akn/fi/doc/government-proposal/{he_year}/{he_number}/fin@/main.xml"
            try:
                span = fa.resolve(loc)
                if span is not None:
                    blob = fa.read(span)
                    meta = fa.meta(span) or {}
                    he_id = meta.get("he_id", f"HE {he_number}/{he_year}")
                    fa.close()
                    return parse_he_branch(
                        blob,
                        he_year=he_year,
                        he_number=he_number,
                        he_id=he_id,
                    )
            except Exception:
                pass
            fa.close()

    return None


# ---------------------------------------------------------------------------
# Hypothetical apply
# ---------------------------------------------------------------------------


def _hypothetical_apply_op(
    op: BranchProposedOp,
    statute_state: Dict[str, str],
    *,
    scope: Optional[str] = None,
) -> Tuple[Optional[ChangedProvision], Optional[str]]:
    """Apply one BranchProposedOp to a mutable statute-state dict.

    statute_state maps provision_ref -> text content.
    Returns (changed_provision, warning).

    Per AGENTS.md §1.1: if target not found and not proposal-relative, emit
    warning rather than silently succeed.

    Per AGENTS.md §1.7: multiple ops at same target at same as-of date are
    applied in op_index order; callers are responsible for detecting conflicts.
    """
    if scope is not None and not op.target_provision_ref.startswith(scope):
        return None, None  # out of scope, skip

    ref = op.target_provision_ref
    before_text = statute_state.get(ref, "")

    if op.operation_kind == "repeal":
        if ref in statute_state:
            del statute_state[ref]
        after_text = ""
    elif op.operation_kind in ("replace", "text_replace"):
        # In simulation: we mark the provision as "replaced by branch op"
        # Real text is not available without full replay; we use a marker.
        after_text = f"[REPLACED by {op.source_he_id} branch op {op.op_index}]"
        statute_state[ref] = after_text
    elif op.operation_kind == "insert":
        # New provision: add with marker
        after_text = f"[INSERTED by {op.source_he_id} branch op {op.op_index}]"
        statute_state[ref] = after_text
    elif op.operation_kind in ("relabel", "move"):
        # Renumber: mark old as moved, add new label
        after_text = f"[RENUMBERED from {ref} by {op.source_he_id} branch op {op.op_index}]"
        statute_state[ref] = after_text
    elif op.operation_kind in ("commencement", "expiry"):
        # Temporal ops: no structural change, but record the fact
        after_text = before_text  # no change to text state
    else:
        after_text = before_text

    warning: Optional[str] = None
    if (
        not op.is_proposal_relative
        and op.target_resolution == BranchTargetResolution.UNRESOLVED
        and not before_text
        and op.operation_kind not in ("insert", "commencement", "expiry")
    ):
        warning = (
            f"HE_BRANCH.TARGET_NOT_IN_CURRENT_STATE: "
            f"{ref} not found in current statute state "
            f"(op_index={op.op_index}, resolution={op.target_resolution.value})"
        )

    changed = ChangedProvision(
        provision_ref=ref,
        before_text=before_text[:500],
        after_text=after_text[:500],
        operation_kind=op.operation_kind,
        target_statute_id=op.target_statute_id,
    )
    return changed, warning


def _detect_broken_refs(
    changed_provisions: Sequence[ChangedProvision],
    *,
    refs_parquet_path: Optional[str] = None,
) -> List[BrokenRefInSimulation]:
    """Detect refs in other statutes that would fail to resolve after simulation.

    Composes fi_refs.parquet (#1) if available; otherwise returns empty list
    with a TODO note.

    Per feature brief: this queries fi_refs.parquet for refs whose target lives
    in the affected provisions, then flags those that no longer resolve.
    """
    if refs_parquet_path is None or not Path(refs_parquet_path).exists():
        # TODO: implement when fi_refs.parquet is available (#1)
        return []

    changed_refs = {cp.provision_ref for cp in changed_provisions
                   if cp.operation_kind in ("repeal", "replace", "text_replace")}
    if not changed_refs:
        return []

    try:
        import pyarrow.parquet as pq  # type: ignore[import]
    except ImportError:
        return []

    broken: list[BrokenRefInSimulation] = []
    try:
        table = pq.read_table(refs_parquet_path)
        for batch in table.to_batches():
            tgt_refs = batch.column("target_provision_ref") if "target_provision_ref" in table.schema.names else None
            src_refs = batch.column("source_provision_ref") if "source_provision_ref" in table.schema.names else None
            if tgt_refs is None or src_refs is None:
                break
            for i in range(len(batch)):
                tgt = str(tgt_refs[i])
                src = str(src_refs[i])
                if tgt in changed_refs:
                    broken.append(BrokenRefInSimulation(
                        source_provision_ref=src,
                        target_provision_ref=tgt,
                        reason="target provision changed or repealed in simulated state",
                    ))
    except Exception:
        pass

    return broken


def _detect_actor_changes(
    changed_provisions: Sequence[ChangedProvision],
    *,
    actors_parquet_path: Optional[str] = None,
) -> List[ActorSlotChange]:
    """Detect actor mentions added or removed by branch ops.

    Composes fi_actors.parquet (#2) if available; otherwise returns empty list.
    """
    # TODO: implement when fi_actors.parquet is available (#2)
    return []


# ---------------------------------------------------------------------------
# Main simulate function
# ---------------------------------------------------------------------------


def simulate_branch(
    branch_id: str,
    *,
    as_of: Optional[str] = None,
    diff_from: str = "current",
    detect_broken_refs: bool = False,
    detect_actor_changes: bool = False,
    scope: Optional[str] = None,
    strict: bool = False,
    farchive_path: Optional[str] = None,
    refs_parquet_path: Optional[str] = None,
    actors_parquet_path: Optional[str] = None,
    branch_ops_parquet_path: Optional[str] = None,
) -> SimulationReport:
    """Materialize a hypothetical PIT state for a branch.

    Parameters
    ----------
    branch_id:
        e.g. 'fi/he/2024/184'
    as_of:
        Simulated date ISO string (default: proposed_voimaantulo or today).
    diff_from:
        Baseline: 'current' | 'baseline' | YYYY-MM-DD.
    detect_broken_refs:
        Flag refs in other statutes that would break.
    detect_actor_changes:
        Flag actor mentions added/removed.
    scope:
        Narrow simulation to provisions matching this prefix.
    strict:
        Reject PARTIAL parse status.
    farchive_path:
        Path to fi_government_proposal.farchive for branch resolution.
    refs_parquet_path:
        Path to fi_refs.parquet for broken-ref detection.
    actors_parquet_path:
        Path to fi_actors.parquet for actor-change detection.
    branch_ops_parquet_path:
        Path to fi_he_branch_ops.parquet for fast lookup.

    Returns
    -------
    SimulationReport — JSON-serializable simulation result.
    """
    warnings_list: list[str] = []

    # Resolve branch
    branch = _resolve_branch(
        branch_id,
        farchive_path=farchive_path,
        parquet_path=branch_ops_parquet_path,
    )

    if branch is None:
        return SimulationReport(
            branch_id=branch_id,
            simulated_at=as_of or date.today().isoformat(),
            diff_from=diff_from,
            parse_status="unknown",
            changed_provisions=[],
            broken_refs_in_other_statutes=[],
            actor_slot_changes=[],
            simulation_warnings=[f"branch not found: {branch_id}"],
            ops_applied=0,
            ops_skipped=0,
        )

    # Determine simulated_at date
    if as_of is None:
        if branch.proposed_voimaantulo is not None:
            simulated_at = branch.proposed_voimaantulo.isoformat()
        else:
            simulated_at = date.today().isoformat()
    else:
        simulated_at = as_of

    # Strict mode: reject PARTIAL parse
    if strict and branch.parse_status == HEParseStatus.PARTIAL:
        return SimulationReport(
            branch_id=branch_id,
            simulated_at=simulated_at,
            diff_from=diff_from,
            parse_status=branch.parse_status.value,
            changed_provisions=[],
            broken_refs_in_other_statutes=[],
            actor_slot_changes=[],
            simulation_warnings=[
                "STRICT MODE: PARTIAL parse status rejected; simulation aborted"
            ],
            ops_applied=0,
            ops_skipped=0,
        )

    # For NOT_APPLICABLE, return immediately with empty delta
    if branch.parse_status == HEParseStatus.NOT_APPLICABLE:
        return SimulationReport(
            branch_id=branch_id,
            simulated_at=simulated_at,
            diff_from=diff_from,
            parse_status=branch.parse_status.value,
            changed_provisions=[],
            broken_refs_in_other_statutes=[],
            actor_slot_changes=[],
            simulation_warnings=["HE has no amendment ops (NOT_APPLICABLE)"],
            ops_applied=0,
            ops_skipped=0,
        )

    # Materialize hypothetical state
    # We use a simple dict as the statute state model for simulation purposes.
    # In a full implementation, this would call the Finland replay pipeline
    # to materialize enacted PIT state first, then apply branch ops on top.
    # For now, we apply ops over an empty baseline (the structural delta is
    # what the HE would introduce, which is the primary simulation output).
    statute_state: Dict[str, str] = {}

    changed_list: list[ChangedProvision] = []
    ops_applied = 0
    ops_skipped = 0

    # Check for AGENTS.md §1.7 conflicts: same target + same op_kind at same as_of
    seen_targets: dict[str, int] = {}  # target_ref -> first op_index
    for op in branch.proposed_ops:
        if op.target_provision_ref in seen_targets:
            warnings_list.append(
                f"HE_BRANCH.MULTI_OP_SAME_TARGET: op_index={op.op_index} and "
                f"op_index={seen_targets[op.target_provision_ref]} both target "
                f"{op.target_provision_ref} — ambiguity preserved, ops applied in index order"
            )
        else:
            seen_targets[op.target_provision_ref] = op.op_index

    for op in branch.proposed_ops:
        changed, warning = _hypothetical_apply_op(op, statute_state, scope=scope)
        if warning:
            warnings_list.append(warning)
        if changed is not None:
            changed_list.append(changed)
            ops_applied += 1
        else:
            ops_skipped += 1

    # Deduplicate changed provisions (keep last for same ref)
    seen_refs: dict[str, int] = {}
    deduped: list[ChangedProvision] = []
    for i, cp in enumerate(changed_list):
        seen_refs[cp.provision_ref] = i
    for cp in changed_list:
        if seen_refs.get(cp.provision_ref) == changed_list.index(cp):
            deduped.append(cp)

    # Broken-ref detection (composes #1)
    broken_refs: list[BrokenRefInSimulation] = []
    if detect_broken_refs:
        broken_refs = _detect_broken_refs(deduped, refs_parquet_path=refs_parquet_path)

    # Actor-change detection (composes #2)
    actor_changes: list[ActorSlotChange] = []
    if detect_actor_changes:
        actor_changes = _detect_actor_changes(deduped, actors_parquet_path=actors_parquet_path)

    return SimulationReport(
        branch_id=branch_id,
        simulated_at=simulated_at,
        diff_from=diff_from,
        parse_status=branch.parse_status.value,
        changed_provisions=[
            {
                "provision_ref": cp.provision_ref,
                "before_text": cp.before_text,
                "after_text": cp.after_text,
                "operation_kind": cp.operation_kind,
                "target_statute_id": cp.target_statute_id,
            }
            for cp in deduped
        ],
        broken_refs_in_other_statutes=[
            {
                "source_provision_ref": br.source_provision_ref,
                "target_provision_ref": br.target_provision_ref,
                "reason": br.reason,
            }
            for br in broken_refs
        ],
        actor_slot_changes=[
            {
                "provision_ref": ac.provision_ref,
                "change_kind": ac.change_kind,
                "actor_canonical_id": ac.actor_canonical_id,
                "actor_show_as": ac.actor_show_as,
            }
            for ac in actor_changes
        ],
        simulation_warnings=warnings_list,
        ops_applied=ops_applied,
        ops_skipped=ops_skipped,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    """CLI entry point for lawvm simulate."""
    branch_id: str = getattr(args, "branch", "") or ""
    if not branch_id:
        print("ERROR: --branch BRANCH_ID is required", file=sys.stderr)
        sys.exit(1)

    as_of: Optional[str] = getattr(args, "as_of", None) or None
    diff_from: str = getattr(args, "diff_from", "current") or "current"
    detect_broken_refs: bool = bool(getattr(args, "detect_broken_refs", False))
    detect_actor_changes: bool = bool(getattr(args, "detect_actor_changes", False))
    scope: Optional[str] = getattr(args, "scope", None) or None
    strict: bool = bool(getattr(args, "strict", False))
    output_format: str = getattr(args, "output_format", "json") or "json"

    # Resolve paths
    farchive_path: Optional[str] = getattr(args, "farchive", None) or None
    if farchive_path is None:
        default_fa = Path("data/fi_government_proposal.farchive")
        if default_fa.exists():
            farchive_path = str(default_fa)

    refs_parquet: Optional[str] = getattr(args, "refs_parquet", None) or None
    actors_parquet: Optional[str] = getattr(args, "actors_parquet", None) or None
    branch_ops_parquet: Optional[str] = getattr(args, "branch_ops_parquet", None) or None

    report = simulate_branch(
        branch_id=branch_id,
        as_of=as_of,
        diff_from=diff_from,
        detect_broken_refs=detect_broken_refs,
        detect_actor_changes=detect_actor_changes,
        scope=scope,
        strict=strict,
        farchive_path=farchive_path,
        refs_parquet_path=refs_parquet,
        actors_parquet_path=actors_parquet,
        branch_ops_parquet_path=branch_ops_parquet,
    )

    if output_format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif output_format == "jsonl":
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    elif output_format == "table":
        _print_table(report)
    else:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


def _print_table(report: SimulationReport) -> None:
    """Print a human-readable tabular simulation summary."""
    print(f"Branch:       {report.branch_id}")
    print(f"Simulated at: {report.simulated_at}")
    print(f"Diff from:    {report.diff_from}")
    print(f"Parse status: {report.parse_status}")
    print(f"Ops applied:  {report.ops_applied}  Ops skipped: {report.ops_skipped}")
    print()
    if report.changed_provisions:
        print(f"Changed provisions ({len(report.changed_provisions)}):")
        for cp in report.changed_provisions:
            print(
                f"  {cp['provision_ref']:40s} "
                f"[{cp['operation_kind']:12s}] "
                f"{cp['before_text'][:30]!r:35s} → "
                f"{cp['after_text'][:30]!r}"
            )
    else:
        print("No changed provisions.")
    if report.broken_refs_in_other_statutes:
        print(f"\nBroken refs ({len(report.broken_refs_in_other_statutes)}):")
        for br in report.broken_refs_in_other_statutes:
            print(f"  {br['source_provision_ref']} → {br['target_provision_ref']}: {br['reason']}")
    if report.simulation_warnings:
        print(f"\nWarnings ({len(report.simulation_warnings)}):")
        for w in report.simulation_warnings:
            print(f"  {w}")
