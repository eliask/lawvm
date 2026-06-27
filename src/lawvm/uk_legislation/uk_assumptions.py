"""United Kingdom (UK) declared-assumption register — the hand-curated, root-committed set.

Sibling of :mod:`lawvm.finland.fi_assumptions` and :mod:`lawvm.sweden.se_assumptions`.
The UK compiler's declared NON-guarantees, as typed
:class:`~lawvm.core.assumption_register.AssumptionRegister` objects rather than
prose scattered across ``notes/UK_REPLAY_REGIME_CONTRACT.md`` § 6,
``notes/UK_HARD_CANARY_FRONTIER.md``, ``notes/MANUAL_COMPILATION_CLAIMS.md``, and
the AGENTS.md § 0 manual-compilation frontier catalogue.

WHY UK specifically. UK is an experimental lane (per ``README.md``). Its
production path is effects-assisted replay against legislation.gov.uk's *current*
consolidated oracle, with two load-bearing frontiers: (1) the ideal
``source_first_enacted_base`` lane does not yet exist — the running lane is
``effects_assisted_replay`` + ``oracle_alignment_adapter`` + ``current_pair_benchmark``
(``notes/UK_REPLAY_REGIME_CONTRACT.md`` § 3); (2) law.com's oracle is single-version
(no PIT historical snapshots), so replay-vs-oracle drift rows are typed frontier
residuals, not fidelity failures. On top of that, AGENTS.md § 0 declares an entire
family of manual-compilation frontier rows (savings/exceptions, contingent
commencement, point-in-time selection, cross-act placement, span-vs-enumeration
ambiguity) where the source does not deterministically specify the result — replay
declines the op rather than guessing the boundary. UK's dormant-checker-probe wires
(materialization-totality, per-op mutation-boundary) emit observation-only
``uk_replay_*_observed`` adjudications because there is no ``strict_profile`` in
UK to flip them to blocking (see ``notes_internal/uk_d1_d7_childtail_findings.md``).

These data-ceiling facts sit underneath every UK replay-vs-oracle agreement claim
the status docs carry as prose; this register makes each a checkable root-committed
object so a missing or surplus declared non-guarantee is detectable, not folklore.

WHAT THIS DOES **NOT** YET DO (honesty boundary — see the core module docstring):
v0 is HAND-CURATED. It does not auto-discover assumptions from prose or scan the
suite's ``xfail`` markers. ``expires_when`` is human-readable, not machine-evaluable.
The root is not yet wired into the pack manifest / compile dossier, and the UK
``notes/UK_*.md`` specs are not in
:data:`~lawvm.core.must_trace.MUST_TRACE_IN_SCOPE_FILES` (UK specs use lowercase
``must`` only — per ``notes_internal/uk_totality_must_trace_verification.md`` —
so adding them yields zero matches; deliberate deferral).

The four entries below encode the four load-bearing UK non-guarantees:

1. **Effects-assisted + oracle-alignment lane is not source-first.** The
   ``--source-first-candidate`` preset marks the cleanest currently-expressible
   lane inside the present runtime; it does NOT produce the ideal
   ``source_first_enacted_base`` lane. Disabling ``--oracle-alignment`` does not
   yet remove every oracle dependency from the UK stack overall
   (``notes/UK_REPLAY_REGIME_CONTRACT.md`` § 6).

2. **Devolved whole-Act repeal territorial-extent slicing is unmaterialised.** A
   Scottish/Welsh/NI instrument that repeals "the Act" only repeals it as it
   extends to that territory. UK does not materialise territorial-extent slices,
   so a UK-wide whole-Act repeal would silently delete the surviving England-and-
   Wales text; the guard blocks lowering and classifies the row as
   ``non_textual_or_out_of_scope`` — permanent frontier until a territorial-extent
   replay model exists.

3. **AGENTS.md § 0 manual-compilation frontier rows are doctrine-unresolved.**
   Savings/exceptions, contingent commencement, point-in-time selection, cross-
   act placement, span-vs-enumeration ambiguity: the source does not
   deterministically specify the result, so replay declines the op until a
   validated claim owns the boundary. The generic catch-all family
   ``uk_manual_frontier_unclassified`` is the witness.

4. **No ``strict_profile`` in UK; dormant-checker probes are observation-only.**
   The materialization-totality, per-op mutation-boundary, and the other wires
   (``mutation_boundary_per_op_probe.py``,
   ``materialization_totality_probe.py``) emit non-blocking
   ``uk_replay_*_observed`` adjudications. A future strict-profile lane could
   flip them to blocking; v0 is discipline-disclosing-first-step posture, not
   enforcement.
"""

from __future__ import annotations

from lawvm.core.assumption_register import AssumptionRegister


def build_uk_assumption_register() -> tuple[AssumptionRegister, ...]:
    """The United Kingdom declared non-guarantees, hand-curated for v0.

    Returned as a sorted-stable tuple so :func:`assumption_register_root`
    yields one deterministic checkable root for the UK declared-assumption set.
    Mirrors :func:`lawvm.finland.fi_assumptions.build_fi_assumption_register`
    and :func:`lawvm.sweden.se_assumptions.build_se_assumption_register`.
    """
    return (
        AssumptionRegister(
            kind="parser_incomplete",
            scope=(
                "UK replay-regime lane: the production path is "
                "`effects_assisted_replay` + `oracle_alignment_adapter` + "
                "`current_pair_benchmark` (notes/UK_REPLAY_REGIME_CONTRACT.md "
                "§ 3). The ideal `source_first_enacted_base` lane does NOT yet "
                "exist; `--source-first-candidate` marks the cleanest currently-"
                "expressible candidate lane inside the present runtime "
                "architecture, NOT the ideal lane. Disabling `--oracle-"
                "alignment` does not yet remove every oracle dependency from "
                "the UK stack overall (notes/UK_REPLAY_REGIME_CONTRACT.md "
                "§ 6) — replay-time mutation is oracle-blind for repeal "
                "semantics, but residual oracle-lane cleanup remains."
            ),
            effect="qualifies",
            expires_when=(
                "the `source_first_enacted_base` lane is operationally built "
                "(semantic replay lane separated from oracle-alignment adapter, "
                "evidence-bundle cache made regime-aware so cached UK bundles "
                "cannot silently cross replay lanes) AND the residual oracle-"
                "lane cleanup is completed (the remaining summary/docs "
                "tightening notes/UK_REPLAY_REGIME_CONTRACT.md § 6 enumerates)."
            ),
            public_message=(
                "LawVM does NOT guarantee that a UK `--source-first-candidate` "
                "run is the ideal source-first semantic lane. It is the cleanest "
                "currently expressible candidate lane inside the present runtime "
                "architecture — effects-assisted replay with metadata backfill "
                "disabled and oracle-alignment adapter disabled at replay time, "
                "but not yet a fully oracle-independent source-first base. The "
                "oracle-alignment adapter runtime does NOT remove every oracle "
                "dependency from the UK stack overall; the residual cleanup is "
                "tracked as a declared boundary, not a solved separation."
            ),
            witness_rule_id="uk_oracle_eid_alignment_adapter",
            finding_refs=(
                "notes/UK_REPLAY_REGIME_CONTRACT.md::§3 Current Reality",
                "notes/UK_REPLAY_REGIME_CONTRACT.md::§6 Current Runtime Separation",
                "notes/UK_REPLAY_REGIME_CONTRACT.md::§7 Current Source-First Candidate Rule",
            ),
        ),
        AssumptionRegister(
            kind="doctrine_unresolved",
            scope=(
                "UK devolved whole-Act repeal territorial-extent slicing: a "
                "Scottish/Welsh/NI instrument that repeals 'the Act' only "
                "repeals it as it extends to that territory. The lowering guard "
                "`uk_effect_devolved_whole_act_repeal_extent_limited_rejected` "
                "(effect_target_prelude.py:522) blocks the whole-Act repeal; "
                "the manual-frontier classifier routes the row to "
                "`uk_manual_frontier_devolved_extent_limited_repeal_out_of_scope` "
                "as `non_textual_or_out_of_scope`. UK does NOT materialise "
                "territorial-extent slices; a UK-wide whole-Act repeal would "
                "silently delete the surviving England-and-Wales text (the §0 "
                "forbidden over-repeal direction)."
            ),
            effect="outside_claim",
            expires_when=(
                "a territorial-extent-aware replay model that materialises the "
                "E&W / Scotland / Wales / NI extent slices of a UK-wide Act lands "
                "and the devolved-whole-Act repeal becomes a per-extent deterministic "
                "lowering rather than a frontier row."
            ),
            public_message=(
                "LawVM does NOT guarantee a materialised territorial-extent-aware "
                "consolidation. A devolved-instrument whole-Act repeal is outside "
                "claim scope: replay blocks the lowering and records the row as "
                "`non_textual_or_out_of_scope` rather than guessing which territorial "
                "slice to delete. The devolved extent frontier is permanent until a "
                "territorial-extent replay model is implemented."
            ),
            witness_rule_id="uk_manual_frontier_devolved_extent_limited_repeal_out_of_scope",
            finding_refs=(
                "tests/test_uk_devolved_whole_act_repeal_extent.py::"
                "test_devolved_whole_act_repeal_frontier_status_is_out_of_scope",
                "tests/test_uk_devolved_whole_act_repeal_extent.py::"
                "test_uk_wide_si_whole_act_repeal_is_not_devolved_frontier",
                "notes/UK_HARD_CANARY_FRONTIER.md",
            ),
        ),
        AssumptionRegister(
            kind="doctrine_unresolved",
            scope=(
                "UK manual-compilation frontier (AGENTS.md § 0): savings/"
                "exceptions, contingent commencement, point-in-time selection, "
                "cross-act placement, span-vs-enumeration ambiguity. The source "
                "does NOT deterministically specify the post-amendment result, "
                "so replay declines the op rather than guessing the savings "
                "scope or affect boundary. The generic catch-all classifier "
                "`uk_manual_frontier_unclassified` (frontier_work_items.py:774) "
                "is the residual that records rows the frontier families did not "
                "individually name; the manual_compile_candidate rows are "
                "promise-to-claim work-items, not compile failures."
            ),
            effect="qualifies",
            expires_when=(
                "a validated manual-claim resolves the row's frontier (savings "
                "scope enumerated, commencement trigger resolved, cross-act "
                "placement proven) — each row climbs the "
                "source-witness→candidate→execution-authorization→replay-proof "
                "promotion chain through a deliberate proof boundary; the "
                "manual-compilation frontier is per-row, not a global unlock."
            ),
            public_message=(
                "LawVM does NOT guarantee that a UK manual-compilation frontier "
                "row will become deterministic lowering. The source text does "
                "not deterministically specify the result (savings scope, "
                "contingent commencement trigger, cross-act placement); replay "
                "declines the op and emits a typed manual_compile_candidate "
                "finding instead. Over-retention is the safe wrong; over-repeal "
                "is the forbidden one (AGENTS.md § 0)."
            ),
            witness_rule_id="uk_manual_frontier_unclassified",
            finding_refs=(
                "AGENTS.md::§0 Prime Directive",
                "notes/MANUAL_COMPILATION_CLAIMS.md",
                "notes_internal/uk_triage/frontier_families_temporal_savings.md::§6",
            ),
        ),
        AssumptionRegister(
            kind="parser_incomplete",
            scope=(
                "UK `strict_profile` absence: UK has no strict_profile lane "
                "(unlike Finland's hardened strict profile). The dormant-checker "
                "probes wired in this frontend (`mutation_boundary_per_op_probe.py`, "
                "`materialization_totality_probe.py`) emit NON-BLOCKING "
                "`uk_replay_*_observed` adjudications — observation-only by "
                "default, not strict failures. A violation that the probe "
                "could block in FI stays as a recorded observation in UK: the "
                "discipline is disclosing-first (the diagnostic fires, the "
                "ledger records it), but the enforcement gate is not yet built."
            ),
            effect="qualifies",
            expires_when=(
                "a UK strict-profile lane is added that flips "
                "`uk_replay_mutation_boundary_per_op_violation_observed` and "
                "`uk_replay_materialization_totality_silent_drop_observed` from "
                "non-blocking adjudications to blocking findings, AND corpus "
                "triage confirms the flipped gate does not regress the bench "
                "beyond the §0 over-retention tolerance."
            ),
            public_message=(
                "LawVM does NOT guarantee that UK enforces the materialization-"
                "totality or per-op mutation-boundary invariants to block. The "
                "probes are wired observation-only (`uk_replay_*_observed`); v0 "
                "is discipline-disclosing-first-step posture, not strict-mode "
                "enforcement. A silent-drop or per-op boundary violation is "
                "RECORDED as an audit event, not a hard compile failure — UK "
                "would benefit from a strict-profile lane before this assumption "
                "expires."
            ),
            witness_rule_id="uk_replay_materialization_totality_silent_drop_observed",
            finding_refs=(
                "src/lawvm/uk_legislation/mutation_boundary_per_op_probe.py::module docstring",
                "src/lawvm/uk_legislation/materialization_totality_probe.py::module docstring",
                "tests/test_uk_mutation_boundary_per_op_probe.py::test_probe_disabled_by_default",
                "tests/test_uk_materialization_totality_probe.py::test_probe_disabled_by_default",
            ),
        ),
    )


__all__ = ["build_uk_assumption_register"]
