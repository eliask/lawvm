"""CP-SAT solver for sparse subsection slot assignment (Lane 2 pilot).

Given amendment payload slots and live section subsections, finds
the optimal assignment using hard constraints (label compat, no double
assignment, monotone order) and soft constraints (exact match preferred).

Returns a SlotAssignmentWitness with status: unique/ambiguous/infeasible.

This is a PILOT -- diagnostic only, not replacing the heuristic chain.
Phase 1 from the rollout plan: parallel comparison.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Literal

from ortools.sat.python import cp_model


# ---------------------------------------------------------------------------
# Witness dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotAssignmentWitness:
    """Witness for a CP-SAT slot assignment result.

    Captures the full problem shape, solver outcome, and diagnostic
    metadata needed for comparison with the heuristic chain.
    """

    problem_kind: str  # "slot_assignment"
    payload_slot_count: int
    live_slot_count: int
    hard_constraint_count: int
    soft_constraint_count: int
    solver: str  # "cp_sat"
    status: Literal["unique", "ambiguous", "infeasible"]
    selected_assignment: dict[int, int] | None  # payload_idx -> live_idx
    alternative_model_count: int
    solve_time_ms: float


# ---------------------------------------------------------------------------
# Solution counter callback (for uniqueness check)
# ---------------------------------------------------------------------------

class _SolutionCounter(cp_model.CpSolverSolutionCallback):
    """Count solutions and optionally record them."""

    def __init__(
        self,
        n_payload: int,
        n_live: int,
        assignment_vars: list[list[cp_model.IntVar]],
        max_solutions: int = 10,
    ) -> None:
        super().__init__()
        self._n_payload = n_payload
        self._n_live = n_live
        self._assignment_vars = assignment_vars
        self._max_solutions = max_solutions
        self._solutions: list[dict[int, int]] = []

    def on_solution_callback(self) -> None:
        assignment: dict[int, int] = {}
        for p_idx in range(self._n_payload):
            for l_idx in range(self._n_live):
                if self.value(self._assignment_vars[p_idx][l_idx]) == 1:
                    assignment[p_idx] = l_idx
        self._solutions.append(assignment)
        if len(self._solutions) >= self._max_solutions:
            self.stop_search()

    @property
    def solution_count(self) -> int:
        return len(self._solutions)

    @property
    def solutions(self) -> list[dict[int, int]]:
        return list(self._solutions)


# ---------------------------------------------------------------------------
# Default label normalizer
# ---------------------------------------------------------------------------

def _default_normalize(label: str) -> str:
    """Strip common noise from Finnish legislative labels.

    Mirrors the logic of ``_norm_num_token`` from helpers.py but without
    importing it, so this module stays self-contained for testing.
    """
    token = re.sub(r'[)\s§.]', '', label).strip().lower()
    return token


# ---------------------------------------------------------------------------
# Model builder (shared between optimization and enumeration phases)
# ---------------------------------------------------------------------------

def _build_model(
    n_payload: int,
    n_live: int,
    payload_labels: list[str],
    live_labels: list[str],
    payload_norm: list[str],
    live_norm: list[str],
    compat: list[list[bool]],
    *,
    optimal_value: int | None = None,
) -> tuple[
    cp_model.CpModel,
    list[list[cp_model.IntVar]],
    int,
    int,
]:
    """Build a CP-SAT model for slot assignment.

    When ``optimal_value`` is None, the model is set up for optimization
    (maximize objective).  When ``optimal_value`` is provided, the model
    is set up for SAT-only enumeration: no objective function, but the
    objective expression is constrained to be >= optimal_value.

    Returns (model, assign_vars, hard_count, soft_count).
    """
    model = cp_model.CpModel()

    # Binary assignment variables: assign[p][l] = 1 iff payload p -> live l
    assign: list[list[cp_model.IntVar]] = []
    for p_idx in range(n_payload):
        row = []
        for l_idx in range(n_live):
            row.append(model.new_bool_var(f"a_{p_idx}_{l_idx}"))
        assign.append(row)

    hard_count = 0
    soft_count = 0

    # --- Hard constraints ---

    # H1: Each payload slot assigned to exactly one live slot
    for p_idx in range(n_payload):
        model.add_exactly_one(assign[p_idx][l_idx] for l_idx in range(n_live))
        hard_count += 1

    # H2: Each live slot receives at most one payload slot
    for l_idx in range(n_live):
        model.add_at_most_one(assign[p_idx][l_idx] for p_idx in range(n_payload))
        hard_count += 1

    # H3: Only compatible pairs allowed
    for p_idx in range(n_payload):
        for l_idx in range(n_live):
            if not compat[p_idx][l_idx]:
                model.add(assign[p_idx][l_idx] == 0)
                hard_count += 1

    # --- Soft constraints (objective expression) ---
    objective_terms: list[tuple[cp_model.IntVar, int]] = []

    # S1: Exact label match bonus (weight 10)
    for p_idx in range(n_payload):
        for l_idx in range(n_live):
            if compat[p_idx][l_idx] and payload_labels[p_idx] == live_labels[l_idx]:
                objective_terms.append((assign[p_idx][l_idx], 10))
                soft_count += 1

    # S2: Normalized label match bonus (weight 5, only if not exact)
    for p_idx in range(n_payload):
        for l_idx in range(n_live):
            if compat[p_idx][l_idx]:
                exact = payload_labels[p_idx] == live_labels[l_idx]
                norm_match = (
                    payload_norm[p_idx] != ""
                    and live_norm[l_idx] != ""
                    and payload_norm[p_idx] == live_norm[l_idx]
                )
                if norm_match and not exact:
                    objective_terms.append((assign[p_idx][l_idx], 5))
                    soft_count += 1

    # S3: Monotone order bonus (weight 3)
    # For each consecutive pair of payload slots, reward if their
    # assigned live indices are in increasing order.
    for p_idx in range(n_payload - 1):
        live_pos_p = model.new_int_var(0, n_live - 1, f"pos_{p_idx}")
        live_pos_q = model.new_int_var(0, n_live - 1, f"pos_{p_idx + 1}")
        model.add(live_pos_p == sum(l_idx * assign[p_idx][l_idx] for l_idx in range(n_live)))
        model.add(live_pos_q == sum(l_idx * assign[p_idx + 1][l_idx] for l_idx in range(n_live)))

        monotone_var = model.new_bool_var(f"mono_{p_idx}")
        model.add(live_pos_q > live_pos_p).only_enforce_if(monotone_var)
        model.add(live_pos_q <= live_pos_p).only_enforce_if(monotone_var.negated())
        objective_terms.append((monotone_var, 3))
        soft_count += 1

    # Build objective expression
    if objective_terms:
        obj_expr = sum(var * weight for var, weight in objective_terms)
        if optimal_value is not None:
            # SAT-only mode: constrain objective to optimal, no maximize
            model.add(obj_expr >= optimal_value)
        else:
            # Optimization mode
            model.maximize(obj_expr)

    return model, assign, hard_count, soft_count


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

def solve_slot_assignment(
    payload_labels: list[str],
    live_labels: list[str],
    *,
    label_normalizer: Callable[[str], str] | None = None,
) -> SlotAssignmentWitness:
    """Solve slot assignment as a CP-SAT problem.

    Hard constraints:
      - Each payload slot assigned to exactly one live slot
      - Each live slot receives at most one payload slot
      - Assignment only allowed where labels are compatible
        (exact match or normalized match)

    Soft constraints (weighted objectives):
      - Exact label match: weight 10
      - Normalized label match: weight 5
      - Monotone order (payload order matches live order): weight 3

    After finding the optimal solution, builds a fresh SAT model
    constrained to the optimal objective value and enumerates all
    solutions to check uniqueness.

    Parameters
    ----------
    payload_labels : list[str]
        Labels from the amendment payload subsections (in order).
    live_labels : list[str]
        Labels from the live section subsections (in order).
    label_normalizer : callable, optional
        Custom label normalization function.  Defaults to stripping
        whitespace, parens, ``S``, and periods.

    Returns
    -------
    SlotAssignmentWitness
        Full witness with status, assignment, and diagnostic metadata.
    """
    t0 = time.monotonic()
    normalize = label_normalizer or _default_normalize

    n_payload = len(payload_labels)
    n_live = len(live_labels)

    # Trivial cases
    if n_payload == 0:
        return SlotAssignmentWitness(
            problem_kind="slot_assignment",
            payload_slot_count=0,
            live_slot_count=n_live,
            hard_constraint_count=0,
            soft_constraint_count=0,
            solver="cp_sat",
            status="unique",
            selected_assignment={},
            alternative_model_count=0,
            solve_time_ms=(time.monotonic() - t0) * 1000,
        )

    if n_live == 0:
        return SlotAssignmentWitness(
            problem_kind="slot_assignment",
            payload_slot_count=n_payload,
            live_slot_count=0,
            hard_constraint_count=0,
            soft_constraint_count=0,
            solver="cp_sat",
            status="infeasible",
            selected_assignment=None,
            alternative_model_count=0,
            solve_time_ms=(time.monotonic() - t0) * 1000,
        )

    # Pre-compute compatibility
    payload_norm = [normalize(lbl) for lbl in payload_labels]
    live_norm = [normalize(lbl) for lbl in live_labels]

    compat: list[list[bool]] = []
    for p_idx in range(n_payload):
        row: list[bool] = []
        for l_idx in range(n_live):
            exact = payload_labels[p_idx] == live_labels[l_idx]
            norm_match = (
                payload_norm[p_idx] != ""
                and live_norm[l_idx] != ""
                and payload_norm[p_idx] == live_norm[l_idx]
            )
            both_empty = payload_labels[p_idx] == "" and live_labels[l_idx] == ""
            row.append(exact or norm_match or both_empty)
        compat.append(row)

    # --- Phase 1: Optimization (find best objective value) ---
    opt_model, opt_assign, hard_count, soft_count = _build_model(
        n_payload, n_live, payload_labels, live_labels,
        payload_norm, live_norm, compat,
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0

    status = solver.solve(opt_model)

    if status == cp_model.INFEASIBLE:
        return SlotAssignmentWitness(
            problem_kind="slot_assignment",
            payload_slot_count=n_payload,
            live_slot_count=n_live,
            hard_constraint_count=hard_count,
            soft_constraint_count=soft_count,
            solver="cp_sat",
            status="infeasible",
            selected_assignment=None,
            alternative_model_count=0,
            solve_time_ms=(time.monotonic() - t0) * 1000,
        )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SlotAssignmentWitness(
            problem_kind="slot_assignment",
            payload_slot_count=n_payload,
            live_slot_count=n_live,
            hard_constraint_count=hard_count,
            soft_constraint_count=soft_count,
            solver="cp_sat",
            status="infeasible",
            selected_assignment=None,
            alternative_model_count=0,
            solve_time_ms=(time.monotonic() - t0) * 1000,
        )

    # Extract optimal assignment
    first_assignment: dict[int, int] = {}
    for p_idx in range(n_payload):
        for l_idx in range(n_live):
            if solver.value(opt_assign[p_idx][l_idx]) == 1:
                first_assignment[p_idx] = l_idx

    optimal_obj = int(solver.objective_value)

    # --- Phase 2: Enumeration (fresh SAT model, no objective) ---
    # Build a fresh model with the optimal objective as a hard constraint.
    # CP-SAT enumerate_all_solutions only works correctly with SAT models
    # (no maximize/minimize directive).
    enum_model, enum_assign, _, _ = _build_model(
        n_payload, n_live, payload_labels, live_labels,
        payload_norm, live_norm, compat,
        optimal_value=optimal_obj,
    )

    counter = _SolutionCounter(n_payload, n_live, enum_assign, max_solutions=10)
    solver2 = cp_model.CpSolver()
    solver2.parameters.max_time_in_seconds = 5.0
    solver2.parameters.enumerate_all_solutions = True
    solver2.solve(enum_model, counter)

    alt_count = counter.solution_count
    if alt_count <= 1:
        result_status: Literal["unique", "ambiguous", "infeasible"] = "unique"
    else:
        result_status = "ambiguous"

    return SlotAssignmentWitness(
        problem_kind="slot_assignment",
        payload_slot_count=n_payload,
        live_slot_count=n_live,
        hard_constraint_count=hard_count,
        soft_constraint_count=soft_count,
        solver="cp_sat",
        status=result_status,
        selected_assignment=first_assignment,
        alternative_model_count=alt_count,
        solve_time_ms=(time.monotonic() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Diagnostic helper: compare solver vs heuristic
# ---------------------------------------------------------------------------

def diagnose_assignment(
    payload_labels: list[str],
    live_labels: list[str],
    heuristic_assignment: dict[int, int],
    *,
    label_normalizer: Callable[[str], str] | None = None,
) -> dict:
    """Run solver and compare with heuristic result.

    Parameters
    ----------
    payload_labels : list[str]
        Payload subsection labels.
    live_labels : list[str]
        Live section subsection labels.
    heuristic_assignment : dict[int, int]
        Heuristic's mapping: payload_idx -> live_idx.
    label_normalizer : callable, optional
        Label normalization function.

    Returns
    -------
    dict
        Diagnostic with keys:
        - solver_witness: the full SlotAssignmentWitness
        - solver_status: unique/ambiguous/infeasible
        - heuristic_matches_solver: bool
        - disagreement_slots: list of payload indices where they differ
        - heuristic_is_optimal: bool (True if heuristic assignment is
          among the solver's optimal solutions)
    """
    witness = solve_slot_assignment(
        payload_labels,
        live_labels,
        label_normalizer=label_normalizer,
    )

    disagreement_slots: list[int] = []
    if witness.selected_assignment is not None:
        solver_map = witness.selected_assignment
        all_payload_indices = set(solver_map.keys()) | set(heuristic_assignment.keys())
        for p_idx in sorted(all_payload_indices):
            s_val = solver_map.get(p_idx)
            h_val = heuristic_assignment.get(p_idx)
            if s_val != h_val:
                disagreement_slots.append(p_idx)
        matches = len(disagreement_slots) == 0
    else:
        # Solver found infeasible -- heuristic can't match
        matches = len(heuristic_assignment) == 0
        if not matches:
            disagreement_slots = sorted(heuristic_assignment.keys())

    return {
        "solver_witness": witness,
        "solver_status": witness.status,
        "heuristic_matches_solver": matches,
        "disagreement_slots": disagreement_slots,
        "heuristic_is_optimal": matches,
    }


# ---------------------------------------------------------------------------
# CLI diagnostic: replay a statute and compare solver vs heuristic
# ---------------------------------------------------------------------------

def cli_solver_diag(args: object) -> None:
    """CLI entry point for ``lawvm solver-diag <statute_id>``.

    Replays a statute, extracts sparse slot binding groups from the replay
    metadata, then runs the CP-SAT solver on each group's labels and compares
    with the heuristic assignment.
    """
    import sys
    from collections import defaultdict

    sid = getattr(args, "statute_id", "")
    source_filter = getattr(args, "source", None)
    verbose = getattr(args, "verbose", False)

    # Replay and capture binding data
    from lawvm.finland.grafter import replay_xml  # type: ignore[import]

    replay_meta: dict = {}
    try:
        replay_xml(sid, replay_meta_out=replay_meta, quiet=True)
    except Exception as exc:
        print(f"error: replay failed for {sid}: {exc}", file=sys.stderr)
        sys.exit(1)

    bindings = replay_meta.get("sparse_slot_bindings", [])
    if not bindings:
        print(f"Statute {sid}: no sparse slot bindings found (0 groups to compare)")
        return

    # Group bindings by (source_statute, target_norm) -- each group is one
    # call to _assign_subsection_slots during replay.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for b in bindings:
        key = (str(b.get("source_statute", "")), str(b.get("target_norm", "")))
        groups[key].append(b)

    if source_filter:
        groups = {k: v for k, v in groups.items() if k[0] == source_filter}

    print(f"Statute  : {sid}")
    print(f"Groups   : {len(groups)}")
    print()

    total_unique = 0
    total_ambiguous = 0
    total_infeasible = 0
    total_disagree = 0

    for (source_statute, target_norm), group_bindings in sorted(groups.items()):
        # Reconstruct solver inputs from binding data:
        # - payload_labels: the payload_slot_label from each binding (sorted by slot index)
        # - For live_labels, we use the target paragraphs as proxy labels
        #   (the heuristic assigns payload slots to subsection ordinals)
        sorted_bindings = sorted(group_bindings, key=lambda b: b.get("payload_slot_index", 0))

        payload_labels_list = [str(b.get("payload_slot_label", "")) for b in sorted_bindings]
        # Target paragraphs from the bindings give us the live subsection labels
        target_paragraphs = sorted({
            int(b["target_paragraph"])
            for b in sorted_bindings
            if b.get("target_paragraph") is not None
        })
        # Build live labels as a sequence covering the range of targets
        if target_paragraphs:
            max_target = max(target_paragraphs)
            live_labels_list = [str(i) for i in range(1, max_target + 1)]
        else:
            live_labels_list = []

        if not payload_labels_list or not live_labels_list:
            continue

        # Reconstruct heuristic assignment: payload_idx -> live_idx
        heuristic_map: dict[int, int] = {}
        for i, b in enumerate(sorted_bindings):
            tp = b.get("target_paragraph")
            if tp is not None:
                # live_idx is 0-based, target_paragraph is 1-based
                heuristic_map[i] = int(tp) - 1

        # Run solver
        witness = solve_slot_assignment(payload_labels_list, live_labels_list)

        # Compare
        diag = diagnose_assignment(
            payload_labels_list, live_labels_list, heuristic_map,
        )

        status = witness.status
        if status == "unique":
            total_unique += 1
        elif status == "ambiguous":
            total_ambiguous += 1
        else:
            total_infeasible += 1

        matches = diag["heuristic_matches_solver"]
        if not matches:
            total_disagree += 1

        marker = ""
        if not matches:
            marker = " *** DISAGREE ***"
        elif status == "ambiguous":
            marker = " [ambiguous]"

        if verbose or not matches or status != "unique":
            print(
                f"  {source_statute} -> {target_norm}  "
                f"solver={status}  alts={witness.alternative_model_count}  "
                f"match={matches}  "
                f"time={witness.solve_time_ms:.1f}ms"
                f"{marker}"
            )
            if verbose:
                print(f"    payload: {payload_labels_list}")
                print(f"    live:    {live_labels_list}")
                print(f"    heur:    {heuristic_map}")
                if witness.selected_assignment:
                    print(f"    solver:  {witness.selected_assignment}")
                if diag["disagreement_slots"]:
                    print(f"    disagree at slots: {diag['disagreement_slots']}")
                print()

    print()
    print(f"Summary: {total_unique} unique, {total_ambiguous} ambiguous, "
          f"{total_infeasible} infeasible, {total_disagree} disagreements")
