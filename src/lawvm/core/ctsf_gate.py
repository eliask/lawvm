"""CTSF residual-set-diff GATE — task #186/#198 (CTSF Phase 3), PRIMARY GATE.

The step that makes the honest metric load-bearing (``FABLE_CORRECTNESS_METRIC.md``
§5 / ``pro_on_fable_notes.txt`` Phase 5): a gate that consumes
``ctsf_residual_report``'s typed residual verdict for a corpus of (replay, oracle)
anchor pairs, diffs the typed-residual SET against a FROZEN baseline, and returns a
gate verdict:

* **FAIL** iff a NEW ``replay_bug`` or ``unknown``-family residual appears versus
  the baseline — the ``has_replay_bug_or_unknown`` predicate (defined in
  ``ctsf_residual_report`` in Phase 2, wired to nothing) evaluated over the DIFF.
  These are the two non-typed, billable-to-replay families; a new one is a genuine
  regression the gate must catch.
* **WARN** iff the scalar residual set MOVED but only in typed families
  (``oracle_editorial_pathology`` / ``temporal_mismatch`` / ``state_index`` /
  ``cnf_unsupported``) — a reportable, evidence-backed move that is NOT a replay
  regression (an oracle-editorial change, a state-index/temporal reclassification,
  a capability-gap shift). The scalar moving is telemetry, not a red gate.
* **PASS** iff the current typed-residual set equals the baseline exactly.

THE FLIP (task #198, this increment): the gate is now PRIMARY / LOAD-BEARING. Its
callable entry (:func:`run_gate`) and CLI (:func:`main`) return NONZERO when the
residual-set diff shows a NEW billable (``replay_bug``/``unknown``) residual vs the
frozen 0-billable ``#183`` FI baseline; a clean run (WARN or PASS) exits 0. The
honest CTSF residual verdict is now the correctness authority; the legacy scalar
bench-regression guard (``lawvm.tools.bench_regression_guard``, an operator-run
comparison over saved bench runs — never an automatic ci.sh stage) is demoted to
TELEMETRY: it is still computable/reportable but is no longer the correctness
authority. The retirement assessment (task #198 follow-up) found there is NO automatic
scalar GATE to retire — the guard is an operator-run CLI diagnostic, not a ci.sh stage
— and the tool is deliberately KEPT because its perf/timing/RSS comparison lanes are
not subsumed by CTSF; see :data:`SCALAR_GATE_STATUS`.

DATA-AWARE PRIMACY (the hard constraint the flip must honor): scoring the real
``#183`` corpus reads the Finlex archive per anchor (slow). The gate therefore GATES
where the corpus is PRESENT (the ``@requires_corpus`` data-present tests are the
authoritative fail-red surface, and :func:`run_gate` fails red under them) and SKIPS
cleanly where the corpus is ABSENT (data-less CI never fails because of this gate).
A fast unit-level surface (baseline-diff logic + synthetic billable injection over
the frozen synthetic corpus) runs in the DEFAULT shard on every ci.sh invocation, so
the gate's fail-red LOGIC is always exercised without paying the full-corpus cost.

Determinism: the gate is a PURE function of ``(frozen anchors, replay projection,
frozen baseline)``. The corpus is an explicit, frozen in-code set of anchor pairs
(``frozen_gate_corpus``); there is no wall-clock, randomness, or filesystem/network
read in the verdict path. Same corpus + same baseline ⇒ same verdict, byte-stable.

WARN LANE (escalation policy, made observable): a move that is ONLY in typed
non-billable families (``oracle_editorial_pathology`` / ``temporal_mismatch`` /
``state_index`` / ``cnf_unsupported``) — or a RESOLVED residual (a count that fell) —
is a WARN: it is reported (printed by the CLI, logged at WARNING via
:data:`_LOG`, and carried on :class:`GateResult`), but it does NOT flip the exit
code. Only a NEW billable is a FAIL. WARN is thus never silent — an oracle-editorial
churn or a state-index reclassification is surfaced as telemetry, not swallowed and
not red.

REAL #183 CORPUS (Phase 3 flip-prep): the gate's *real* corpus is no longer the 4
synthetic anchor pairs — it is the frozen FI ``#183`` touch-relation anchor set
(``REAL_ANCHOR_CORPUS_SIDS``, an explicit content-pinned list of statute ids). For
each statute the ``fi_anchor_manifest`` attribution engine
(``attribute_statute``) scores every published-consolidation anchor over the
statute's life and emits typed ``TouchObservation`` verdicts; those verdicts map
1:1 onto the CTSF residual families via the engine's own ``_VERDICT_TO_FAMILY``
(``candidate_replay_bug_persistent_post_touch → replay_bug``, ``untyped → unknown``,
``oracle_suspect_* → oracle_editorial_pathology``,
``temporal_mismatch_commensurability → temporal_mismatch``). The gate diffs THAT
typed-residual set against the frozen baseline — so the honest metric now measures
the real published corpus, and any NEW ``replay_bug``/``unknown`` the touch relation
localizes is a hard FAIL.

Freezing the real corpus (reproducibility discipline): the corpus is content-pinned
two ways. (1) The statute-id list is an explicit sorted tuple in code — no live
enumeration, so the *membership* of the corpus is frozen. (2) The committed baseline
(``ctsf_gate_residual_baseline.json``) is the frozen snapshot of the typed-residual
set that list produces; the gate diffs against it. Scoring the real corpus DOES read
the Finlex corpus archive (it re-derives the touch relation per anchor via replay),
so it is NOT the wall-clock-free pure path the synthetic corpus is — but it *is*
deterministic (same corpus bytes ⇒ same observations ⇒ same residual set, verified),
and a corpus refresh that moves the residual set is caught as a preregistered
predict-then-compare event (the committed-baseline freshness test tells you to
regenerate, exactly the #137 silent-baseline-drift guard). Tests that score the real
corpus SKIP cleanly when the Finlex archive is absent, so the gate's unit surface
(diff logic, synthetic corpus, round-trip) stays corpus-free and CI-green.

ORACLE-CHURN BASELINE-REFRESH DISCIPLINE (enforced): a baseline refresh is a
preregistered predict-then-compare event. The committed baseline
(``ctsf_gate_residual_baseline.json``) must equal the current real-corpus residual
set — the ``@requires_corpus`` freshness test
(``test_committed_baseline_matches_real_corpus``) FAILS the moment a corpus/data
refresh moves residuals, so the set cannot drift silently: a legitimate move must be
confirmed and the baseline DELIBERATELY regenerated
(``uv run python -m lawvm.core.ctsf_gate --update-baseline``). This is the #137
silent-baseline-drift guard, now guarding the primary gate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

_LOG = logging.getLogger(__name__)

from lawvm.core.ctsf_corpus_cache import memoize_default_corpus
from lawvm.core.ctsf_residual_report import (
    RESIDUAL_VERDICT_FAMILIES,
    CTSFResidualReport,
    residual_set_report,
)
from lawvm.core.ctsf_state_index import StateIndex
from lawvm.semantic.model import SemanticStructureFacet, SemanticStructureNode

# The gate baseline artifact (committed, frozen). The current typed-residual set of
# ``frozen_gate_corpus`` is snapshotted here; the gate diffs against it.
GATE_BASELINE_PATH = Path("tests/data/ctsf_gate_residual_baseline.json")

GATE_VERSION = "v0"

# The gate is now PRIMARY / load-bearing (task #198): a NEW billable residual vs the
# frozen baseline flips the exit code red. (Was "parallel/report" in Phase 3.)
GATE_MODE = "PRIMARY"

# The legacy scalar bench-regression guard (``lawvm.tools.bench_regression_guard``) is
# an operator-run comparison over saved bench runs — NOT an automatic ci.sh stage. As
# of the flip it is demoted to telemetry: still computable/reportable, no longer the
# correctness authority (the CTSF residual verdict is).
#
# GATE-RETIREMENT ASSESSMENT (task #198 follow-up, completed): the scalar guard has NO
# automatic GATE role to retire. It is invoked only by the operator-facing
# ``lawvm bench-regression-guard`` CLI subcommand (its ``main`` does
# ``raise SystemExit(run_guard(...))`` — an exit code returned to a human operator) and
# by its own unit tests over synthetic ``tmp_path`` fixtures. It is not wired into
# ``scripts/ci.sh``, any Makefile/tox/pre-commit/CI-workflow, or any test that fails red
# on committed real bench data. So there is no fail-red CI stage to remove: CTSF (now
# PRIMARY, covering FI/EE/UK real anchor corpora) already IS the sole automatic
# correctness authority.
#
# The operator tool is KEPT (not retired) because it is still load-bearing as a
# performance/telemetry diagnostic that CTSF does NOT subsume: CTSF is a correctness
# residual-SET diff with no timing/memory lane, whereas the guard offers
# ``--max-duration-regressions`` (wall-clock), ``--max-rss-regressions`` (process
# high-water RSS), and ``--max-phase-regressions``/``--phase`` (per-phase timing)
# comparisons over two arbitrary saved runs across FI/EE/UK. It is also a documented CLI
# contract (``JURISDICTION_CLI_TOOLING_CONTRACT.md``, ``UK_REPLAY_LIVING_SPEC.md``).
# Retirement is therefore not a matter of removing dead gating — there is none — and
# deleting the tool would drop live diagnostic capability with no replacement.
SCALAR_GATE_STATUS = "telemetry_no_gate_to_retire_tool_kept_as_diagnostic"

# The two billable-to-replay families whose APPEARANCE (a new one vs baseline) is a
# hard FAIL. Everything else is typed, evidence-backed, and non-billable to replay —
# a move in those is a WARN (telemetry), never a red gate.
FAIL_FAMILIES: tuple[str, ...] = ("replay_bug", "unknown")

GateVerdict = Literal["PASS", "WARN", "FAIL"]


# ---------------------------------------------------------------------------
# The frozen corpus — an explicit, deterministic set of anchor pairs. No
# wall-clock, no randomness, no filesystem/network. This is the corpus the gate
# scores; freezing it in-code makes the gate a pure function (the corpus is part of
# the "frozen anchors" input the design mandates).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateAnchor:
    """One frozen (replay, oracle) anchor pair the gate scores.

    ``replay`` / ``oracle`` are the two logical-IR renderings to compare;
    ``replay_index`` / ``oracle_index`` are the per-side STATE_INDEX coordinates
    (omitted ⇒ commensurable, straight to CTSF content comparison — the fail-open
    default). Built one-sided, frozen, deterministic.
    """

    sid: str
    replay: SemanticStructureNode
    oracle: SemanticStructureNode
    replay_index: Optional[StateIndex] = None
    oracle_index: Optional[StateIndex] = None

    def report(self) -> CTSFResidualReport:
        return residual_set_report(
            self.replay,
            self.oracle,
            sid=self.sid,
            replay_index=self.replay_index,
            oracle_index=self.oracle_index,
        )


def _wording(text: str) -> tuple[SemanticStructureFacet, ...]:
    return (SemanticStructureFacet(kind="wording", text=text),)


def _sec(label: str, *, text: str = "") -> SemanticStructureNode:
    return SemanticStructureNode(
        kind="section", label=label, facets=_wording(text) if text else ()
    )


def frozen_gate_corpus() -> tuple[GateAnchor, ...]:
    """The frozen, deterministic corpus of anchor pairs the gate scores.

    Explicit in-code so the gate has NO hidden input: each anchor is a fixed
    (replay, oracle) pair chosen to exercise every verdict lane the gate reasons
    over — a clean CTSF-equal pass, a state-index short-circuit (typed, non-billable),
    an editorial-elision agreement (dot-leaders / label-redundant ordinal), a
    capability-gap (CNF_UNSUPPORTED) row. The baseline snapshots THIS corpus's
    typed-residual set; the gate re-scores it and diffs.

    Deliberately NO ``unknown``/``replay_bug`` row in the baseline corpus: the
    frozen baseline is the "clean" residual set, so the gate's FAIL lane is proven
    by INJECTING a synthetic new billable residual in the tests, not baked into the
    baseline.
    """
    anchors: list[GateAnchor] = [
        # 1. CTSF-equal after editorial normalization (dot-leaders elided) — no
        #    residual at all; the clean-pass lane.
        GateAnchor(
            sid="ctsf_gate/dot_leaders",
            replay=_sec("5", text="maksu 20"),
            oracle=_sec("5", text="maksu.......... 20"),
        ),
        # 2. Label-redundant momentti ordinal — CTSF-equal via a witnessed
        #    editorial elision; still no residual (agreement).
        GateAnchor(
            sid="ctsf_gate/momentti_ordinal",
            replay=SemanticStructureNode(
                kind="subsection", label="2", facets=_wording("momentin teksti")
            ),
            oracle=SemanticStructureNode(
                kind="subsection", label="2", facets=_wording("2. momentin teksti")
            ),
        ),
        # 3. State-index incommensurable (oracle embedded a future-effective
        #    version) — short-circuits to a typed ``state_index`` residual BEFORE
        #    content comparison; the content divergence is NOT billed. Non-billable.
        GateAnchor(
            sid="ctsf_gate/state_index_future_effective",
            replay=_sec("5", text="maksu 20"),
            oracle=_sec("5", text="maksu 30"),
            replay_index=StateIndex(as_of="2020-01-01"),
            oracle_index=StateIndex(as_of="2020-06-01", effective_date="2021-06-01"),
        ),
        # 4. Capability gap — the oracle carries a logical table CTSF v0 cannot
        #    address; a typed ``cnf_unsupported`` residual (a standing gap, not a
        #    content diff). Non-billable.
        GateAnchor(
            sid="ctsf_gate/cnf_table",
            replay=_sec("9", text="t"),
            oracle=_cnf_table_oracle(),
        ),
    ]
    return tuple(anchors)


def _cnf_table_oracle() -> SemanticStructureNode:
    from lawvm.core.table_model import TableBody

    return SemanticStructureNode(
        kind="section",
        label="9",
        facets=(
            SemanticStructureFacet(
                kind="wording",
                text="t",
                tables=(TableBody(table_id="t1", caption="", columns=(), rows=()),),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# The REAL #183 touch-relation corpus — the gate's production corpus.
#
# An explicit, content-pinned list of FI statute ids. For each, the #183
# ``fi_anchor_manifest`` attribution engine scores every published-consolidation
# anchor over the statute's life and emits typed ``TouchObservation`` verdicts,
# which project 1:1 onto the CTSF residual families. This is the honest corpus the
# gate measures — real published consolidations, not synthetic pairs.
#
# The list is FROZEN in code (membership is content-pinned; no live enumeration) and
# curated to exercise every residual family the gate reasons over, INCLUDING the two
# billable FAIL families the honest metric exists to expose: the touch relation
# localizes a real ``replay_bug`` (persistent-post-touch divergence) and ``unknown``
# (untyped divergence) on ``1969/10``. Those are acknowledged in the frozen baseline
# as the current state — the gate FAILs on NEW ones vs it, never hides the standing
# ones. Selection is documented (which family each carries) so a reader can audit the
# coverage; a data refresh that moves the set is a preregistered event.
# ---------------------------------------------------------------------------


# Jurisdiction of the FI real corpus. FI was the first #183 touch-relation
# attribution engine (``fi_anchor_manifest``); EE (``ee_anchor_manifest``, #205), UK
# (``uk_anchor_manifest``, #205), and EU (``eu_anchor_manifest``, #204 — flipped to
# the REAL oracle-touch model over published sector-0 consolidations by #221, with a
# conserved-apply fallback lane for the one zero-consolidation base) each have their
# own analogue and participate as the SECOND / THIRD / FOURTH
# jurisdiction corpora below.
# See notes_internal/CTSF_PHASE3_REALANCHORS_2026_07_03.md.
REAL_ANCHOR_JURISDICTION = "finland"

# The frozen, content-pinned statute-id corpus. Sorted, explicit — the corpus
# membership is part of the "frozen anchors" input the design mandates. Each entry
# is annotated with the residual family(ies) it contributes at freeze time so the
# coverage is auditable (counts live in the committed baseline, not here).
REAL_ANCHOR_CORPUS_SIDS: tuple[str, ...] = (
    "1966/258",   # oracle_editorial_pathology + temporal_mismatch (2 anchors, 22 obs)
    "1969/10",    # replay_bug(1) + unknown(2) + oracle_editorial_pathology(8) — the
    #               headline: real billable residuals the touch relation localizes
    "1978/734",   # temporal_mismatch (commensurability-suspect anchors)
    "1987/380",   # oracle_editorial_pathology + temporal_mismatch
    "1990/845",   # temporal_mismatch
    "1999/731",   # scored, clean (no observation) — carries a clean real sid
    "2000/812",   # oracle_editorial_pathology + temporal_mismatch (10 anchors)
    "2002/1248",  # temporal_mismatch
    "2011/1287",  # scored, clean — a second clean real sid
)


# ---------------------------------------------------------------------------
# The REAL #183 touch-relation corpus — ESTONIA (task #205, the second jurisdiction).
#
# EE anchors are the published Riigi Teataja *terviktekst* versions of a statute
# family (``grupi_id``): a content-addressed consolidated-text snapshot at one
# effective date, exactly analogous to Finland's consolidation snapshots. For each
# grupi_id the ``ee_anchor_manifest`` attribution engine replays base→as_of per
# anchor and emits typed ``TouchObservation`` verdicts that project 1:1 onto the
# same CTSF residual families (via the shared ``_VERDICT_TO_FAMILY``). The gate
# diffs THAT set against the frozen EE baseline.
#
# This corpus is curated to be 0-BILLABLE (no replay_bug/unknown) — the honest
# steady state, mirroring the FI baseline. It DOES exercise the typed non-billable
# lane (``1055878`` carries ``oracle_editorial_pathology``). Two deep multi-amendment
# EE chains that once surfaced genuine replay text-preservation bugs (``1022254`` §2,
# ``1048615`` §1) were the #205 excluded defects; #208 FIXED both at the root (nested
# „…" quote handling in the EE amendment-instruction parser and the omnibus intro-
# fragment title matcher), proved byte-exact against the oracle across every window,
# and PROMOTED them into this frozen 0-billable corpus — the metric convicted, the
# fix cleared the conviction. See the #208 deliverable report / notes_internal.
# ---------------------------------------------------------------------------

REAL_ANCHOR_EE_JURISDICTION = "estonia"

# The frozen, content-pinned EE grupi_id corpus (sorted, explicit — membership is
# part of the frozen input). Each is a real statute family with a genuine amendment
# chain that replays; annotated with the residual family it contributes at freeze.
REAL_ANCHOR_EE_CORPUS_SIDS: tuple[str, ...] = (
    "1000509",   # scored clean (1 window)
    "1000762",   # scored clean (1 window)
    "1002539",   # scored clean (1 window)
    "1010163",   # scored clean (1 window)
    "1010901",   # scored clean (1 window)
    "1022254",   # scored clean (2 windows) — was a genuine replay bug (#208): §2's
    #               inserted COFOG clause was truncated at a nested „…" quote; fixed
    #               in the EE amendment-instruction parser, now byte-matches oracle
    "1048615",   # scored clean (5 windows) — was a genuine replay bug (#208): the
    #               omnibus §14 „punktiga 18" list-item insert was orphaned because
    #               the target statute's own nested-quote title failed intro-fragment
    #               matching; fixed, all 5 windows now byte-match oracle
    "1053073",   # scored clean (2 windows — multi-anchor touch relation)
    "1055383",   # scored clean (1 window)
    "1055878",   # oracle_editorial_pathology(2) — the typed non-billable WARN lane
    "1057989",   # scored clean (2 windows — multi-anchor touch relation)
)

# The committed EE baseline artifact (frozen, sibling of the FI one).
GATE_EE_BASELINE_PATH = Path("tests/data/ctsf_gate_ee_residual_baseline.json")


# ---------------------------------------------------------------------------
# The REAL #183 touch-relation corpus — UNITED KINGDOM (task #205, THIRD jurisdiction).
#
# A UK anchor is the genuine content-addressed replay WINDOW published per act:
# ``enacted`` (the statute as originally enacted — the replay base) → ``current`` (the
# single revised/consolidated in-force oracle — the as_of). UK does NOT publish an
# enumerable effective-date-addressed chain of consolidated versions (verified: 0
# dated PIT locators in the Farchive; the multiple observations of a ``/data.xml``
# locator are wall-clock CRAWL timestamps, not legal effective dates), so — unlike
# EE's multi-version terviktekst chain — each UK act is a 2-node replay chain
# (enacted, current). For each act the ``uk_anchor_manifest`` attribution engine
# replays enacted→current and emits typed ``TouchObservation`` verdicts that project
# 1:1 onto the same CTSF residual families (via the shared ``_VERDICT_TO_FAMILY``).
# The gate diffs THAT set against the frozen UK baseline.
#
# UK's per-key surface is eId PRESENCE (canonicalized + normalized via
# ``normalize_uk_replay_compare_eids``) — UK's own commensurable compare-eId surface,
# NOT byte-exact per-eId text (documented in uk_anchor_manifest: UK text is only an
# averaged ratio in uk-bench, never a per-key binary). This preserves the same-
# dimension-touch principle: a penalized eId (absent from replay) that replay TOUCHED
# (added/removed in the window) and left diverged is a candidate replay bug; one
# replay never touched is oracle-side.
#
# This corpus is curated 0-BILLABLE (no replay_bug/unknown) — the honest steady state,
# mirroring FI/EE. UK acts whose enacted→current replay surfaces GENUINE billable
# residuals (a replay-touched eId the oracle carries that replay drops) are
# DELIBERATELY EXCLUDED from the baseline: those are real defects to fix, not to freeze
# — leaving them convicting is the point of the metric. See the deliverable report /
# notes_internal for the itemized excluded-bug list.
# ---------------------------------------------------------------------------

REAL_ANCHOR_UK_JURISDICTION = "united_kingdom"

# The frozen, content-pinned UK statute-id corpus (sorted, explicit — membership is
# part of the frozen input). Each is a real act with a genuine amendment chain that
# replays enacted→current 0-billable; annotated with the residual family it
# contributes at freeze time so the coverage is auditable.
REAL_ANCHOR_UK_CORPUS_SIDS: tuple[str, ...] = (
    "asp/2010/3",     # oracle_editorial_pathology(11) — Scottish act, WARN lane
    "nia/2000/4",     # oracle_editorial_pathology(12) — NI act, cross-type coverage
    "ukpga/1971/38",  # oracle_editorial_pathology(49)
    "ukpga/1990/10",  # oracle_editorial_pathology(59)
    "ukpga/2000/12",  # oracle_editorial_pathology(6)
    "ukpga/2010/10",  # oracle_editorial_pathology(13)
    "ukpga/2010/17",  # scored clean (perfect enacted→current replay, 0 obs)
    "ukpga/2012/14",  # oracle_editorial_pathology(65)
    "ukpga/2020/1",   # scored clean (perfect enacted→current replay, 0 obs)
)

# The committed UK baseline artifact (frozen, sibling of the FI/EE ones).
GATE_UK_BASELINE_PATH = Path("tests/data/ctsf_gate_uk_residual_baseline.json")


# ---------------------------------------------------------------------------
# The REAL touch-relation corpus — EUROPEAN UNION (task #204 → #221, FOURTH
# jurisdiction).
#
# EU joined the gate (#204) on a WEAKER surface than FI/EE/UK — the conserved
# apply fold's own invariant over single (amender, base) windows — because the EU
# Cellar Farchive then stored NO sector-0 consolidation and NO dated amendment
# DAG: there was no published oracle to diff against. #221 CLOSED that gap and
# FLIPPED EU onto the same oracle-touch surface FI/EE/UK/NZ use:
#
#   * ``scripts/acquire_eu_consolidations.py`` stored the 75 PUBLISHED dated
#     sector-0 consolidations of 8 of the 9 frozen bases offline
#     (``cellar://celex/{base}/{YYYYMMDD}/eng/fmx4``);
#   * the dated amendment DAG of those bases is frozen as a content-pinned edge
#     table in ``eu_anchor_manifest`` (``REAL_ANCHOR_EU_AMENDMENT_CLOSURE``);
#   * per stored consolidation ``(base, as_of)`` the MULTI-AMENDER PIT closure is
#     replayed (graft base → lower every amender effective by as_of → legal
#     ordering → conserved apply fold) and diffed per-article against the stored
#     consolidation; Finland's NEUTRAL ``attribute_divergences`` types every
#     divergence via the touch relation (the shared ``_VERDICT_TO_FAMILY``).
#
# The consolidation stays an EDITORIAL witness ("no legal value" — the
# ``eu_oracle_divergence`` honesty regime): divergences are TYPED, never repaired
# toward. Anchors whose closure window is not fully replayable (missing amender
# bytes / unlowered instructions / typed op-skips) are commensurability-marked so
# their divergences type ``temporal_mismatch`` — replay knows it under-applied,
# so no side is convicted there; each gap cause is ALSO a typed ``cnf_unsupported``
# / ``temporal_mismatch`` EU observation, so the capability/acquisition frontier
# is explicit in the residual set, never buried. Apply RAISEs and conservation
# violations stay BILLABLE (replay_bug / unknown) over the full closure replay.
#
# The metric earned its keep at the flip: the very first fully-covered window
# (32022R2309@20230216) convicted TWO genuine replay bugs the conserved-apply
# lane had scored "clean" — an omnibus cross-target misapplication (32023R0331's
# Regulation-356/2010 instruction landing in 2309's Article 4) and the quoted
# payload carrying its own "Article N" heading + the instruction's trailing
# period. All were fixed at ROOT (``fmx4_amendment_grammar``: foreign-target
# guard, heading strip, QUOT.END payload boundary), after which that window is
# byte-clean on the commensurable surface. The frozen baseline is 0-billable.
# ---------------------------------------------------------------------------

REAL_ANCHOR_EU_JURISDICTION = "european_union"

# The 8 oracle-touch bases (published consolidations stored) live in
# ``eu_anchor_manifest.REAL_ANCHOR_EU_ORACLE_BASES`` together with the frozen
# closure table; imported lazily inside ``score_eu_real_corpus`` (the manifest
# module imports replay machinery the gate's unit surface must not pay for).

# The conserved-apply FALLBACK lane — DOCUMENTED: ``32017R1576`` has ZERO
# published sector-0 consolidations (verified live at #221 acquisition), so no
# oracle-touch surface exists for it; its replay window keeps the #204
# conserved-apply invariant scoring. The other 9 #204 chains were SUPERSEDED by
# the oracle-touch surface (their bases' full multi-amender closures — a strict
# superset of those single windows — are now replayed per consolidation anchor,
# with the same apply-raise / conservation billables still enforced).
#
# DIRECTION CORRECTION (#221): #204 froze this window INVERTED
# (``("32014R0540", "32017R1576")`` — 540/2014 "amending" 2017/1576). Legally
# 2017/1576 amends 540/2014 (sound-level regulation), and the old lane's
# ``cnf_unsupported(1)`` was a FALSE-POSITIVE op (a substantive 540/2014 article
# mis-lowered as an amendment, then typed-skipped at apply) that the #221
# omnibus foreign-target guard now suppresses at lowering. The corrected window
# currently lowers 0 ops — 2017/1576 uses the multi-point single-article
# instruction shape ("Regulation (EU) No 540/2014 is amended as follows: (1) …")
# the grammar does not yet iterate — so it scores a typed, VISIBLE
# ``cnf_unsupported`` curation row (an ERROR-status chain is never a silent
# clean; see ``score_eu_real_corpus``).
REAL_ANCHOR_EU_CORPUS_CHAINS: tuple[tuple[str, str], ...] = (
    ("32017R1576", "32014R0540"),  # cnf_unsupported(1) — zero-op lowering gap row
)

# The committed EU baseline artifact (frozen, sibling of the FI/EE/UK ones).
GATE_EU_BASELINE_PATH = Path("tests/data/ctsf_gate_eu_residual_baseline.json")


@dataclass(frozen=True, slots=True)
class EUCorpusScore:
    """Diagnostics-preserving EU CTSF score.

    ``residuals`` is the historical gate input. ``typed_skip_bucket_counts`` is
    a diagnostics-only I1 account over EU typed-op skips; it never changes the
    residual-set diff gate semantics.
    """

    residuals: dict[str, dict[str, int]]
    typed_skip_bucket_counts: dict[str, dict[str, int]]


def eu_anchor_corpus_available() -> bool:
    """True iff the EU Cellar Farchive backing the EU real corpus is present.

    Scoring the EU #204 corpus re-derives the replay-conservation residual per window
    via offline replay, which reads the EU Cellar Farchive. When it is absent (a
    corpus-free CI checkout) the EU real-corpus tests SKIP; the gate's unit surface
    stays corpus-free and always runs.
    """
    try:
        from lawvm.tools.eu_anchor_manifest import _default_db

        return _default_db().exists()
    # An availability PROBE: any archive-open failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline).
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error
    except Exception:  # noqa: BLE001
        return False


def _eu_bucket_summary_dict(summaries: Iterable[Any]) -> dict[str, int]:
    """Project typed skip bucket summaries into a stable JSON/count shape."""
    return {
        str(summary.skip_bucket): int(summary.count)
        for summary in summaries
        if int(summary.count)
    }


@memoize_default_corpus
def score_eu_real_corpus_account(
    chains: Iterable[tuple[str, str]] | None = None,
) -> EUCorpusScore:
    """Score the EU corpus into residuals plus diagnostics-only skip buckets.

    TWO lanes, one diffable set:

    * ORACLE-TOUCH (primary, #221): for each base in
      ``eu_anchor_manifest.REAL_ANCHOR_EU_ORACLE_BASES``, replay the multi-amender
      PIT closure against every STORED published consolidation and project the
      typed touch/gap observations into their CTSF residual families
      (``EUOracleAttribution.family_counts`` — FI's neutral verdict map for the
      touch relation, the EU verdict map for the closure-gap/billable extras).
      The sid is the base CELEX.
    * CONSERVED-APPLY FALLBACK (#204, documented): for each ``(amender, base)``
      in ``REAL_ANCHOR_EU_CORPUS_CHAINS`` (bases with ZERO published
      consolidations), score the apply-fold conservation invariant as before.
      The sid stays ``amender->base``.

    ``residuals`` preserves the same diffable ``{sid: {family: count}}`` shape
    as :func:`score_eu_real_corpus`. ``typed_skip_bucket_counts`` is a sibling
    diagnostics-only account over EU typed-op skips, keyed by the same sid and
    projected from typed evidence carriers rather than rendered strings.
    Deterministic in sid order. Reads the EU Cellar Farchive (offline replay);
    deterministic given the frozen corpus bytes + the frozen closure table.

    ``chains`` overrides the FALLBACK lane only (kept for the synthetic-set
    callers/tests); the oracle-touch bases are always scored.
    """
    from farchive import Farchive

    from lawvm.tools.eu_anchor_manifest import (
        _VERDICT_TO_FAMILY as _EU_VERDICT_TO_FAMILY,
        _default_db as _eu_default_db,
        EUChainRef,
        REAL_ANCHOR_EU_ORACLE_BASES,
        attribute_base_consolidations,
        attribute_chain,
        summarize_eu_typed_op_skip_buckets,
    )

    corpus = tuple(chains) if chains is not None else REAL_ANCHOR_EU_CORPUS_CHAINS
    # Open ONE archive handle for the whole corpus so each window's replay reuses it.
    archive = Farchive(str(_eu_default_db()), readonly=True)
    try:
        out: dict[str, dict[str, int]] = {}
        skip_buckets: dict[str, dict[str, int]] = {}
        for oracle_base in REAL_ANCHOR_EU_ORACLE_BASES:
            oracle_attr = attribute_base_consolidations(oracle_base, archive=archive)
            out[oracle_attr.sid] = oracle_attr.family_counts()
            buckets = _eu_bucket_summary_dict(oracle_attr.typed_skip_bucket_counts())
            if buckets:
                skip_buckets[oracle_attr.sid] = buckets
        for amender, base in corpus:
            attr = attribute_chain(EUChainRef(amender=amender, base=base), archive=archive)
            families: dict[str, int] = {}
            for obs in attr.observations:
                family = _EU_VERDICT_TO_FAMILY[obs.verdict]
                families[family] = families.get(family, 0) + 1
            if attr.status != "OK" and not attr.observations:
                # A window that could not replay at all (base not graftable /
                # amender not stored / zero ops lowered) must NEVER project to
                # an empty — clean-looking — row: it is a typed, visible
                # capability/curation gap (non-billable), not an agreement.
                families["cnf_unsupported"] = families.get("cnf_unsupported", 0) + 1
            out[attr.sid] = {fam: n for fam, n in sorted(families.items()) if n}
            buckets = _eu_bucket_summary_dict(
                summarize_eu_typed_op_skip_buckets(attr.observations)
            )
            if buckets:
                skip_buckets[attr.sid] = buckets
        return EUCorpusScore(
            residuals=dict(sorted(out.items())),
            typed_skip_bucket_counts=dict(sorted(skip_buckets.items())),
        )
    finally:
        archive.close()


def score_eu_real_corpus(
    chains: Iterable[tuple[str, str]] | None = None,
) -> dict[str, dict[str, int]]:
    """Score the EU corpus into the historical residual-set gate shape."""
    return score_eu_real_corpus_account(chains).residuals


def _eu_baseline_payload(
    residuals: dict[str, dict[str, int]],
    *,
    typed_skip_bucket_counts: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    from lawvm.tools.eu_anchor_manifest import REAL_ANCHOR_EU_ORACLE_BASES

    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — EUROPEAN UNION (#183/#204/#221). "
            "Frozen typed-residual set of the REAL EU corpus, keyed {sid: {family: "
            "count}} with only non-zero families retained. TWO lanes: (1) the #221 "
            "ORACLE-TOUCH lane (corpus_oracle_bases, sid = base CELEX) — per stored "
            "PUBLISHED sector-0 consolidation the multi-amender PIT closure is "
            "replayed offline and diffed per-article against the consolidation; "
            "Finland's neutral touch-relation calculus types every divergence "
            "(oracle_editorial_pathology / temporal_mismatch for commensurability-"
            "limited gap windows), closure acquisition/lowering gaps are explicit "
            "typed cnf_unsupported/temporal_mismatch rows, and apply RAISEs / "
            "conservation violations stay billable; (2) the #204 conserved-apply "
            "FALLBACK lane (corpus_chains, sid = amender->base) for the one base with "
            "ZERO published consolidations (32017R1576). The consolidation is an "
            "EDITORIAL witness (no legal value) — divergences are typed, never "
            "repaired toward. The gate FAILs iff a NEW replay_bug/unknown residual "
            "appears vs this set; WARNs on a typed non-billable move. This corpus is "
            "0-BILLABLE at freeze: the flip itself convicted two genuine replay bugs "
            "(omnibus cross-target misapplication + quoted-payload boundary defects "
            "on 32022R2309@20230216) which were fixed at root in "
            "fmx4_amendment_grammar before freezing. #9 EU-AMENDER-ACQUISITION wave: "
            "the 3 truly-missing amenders + 34 wrong-manifestation-item stores (a DOC "
            "envelope / ANNEX member / TIFF stored in lieu of the ACT body) were "
            "durably re-acquired ACT-rooted via eu_acquire.resolve_act_body's sibling-"
            "DOC_N walk (scripts/acquire_eu_amenders.py); every newly-applied "
            "instruction was adjudicated — several windows moved TOWARD the oracle "
            "(temporal_mismatch dropped) and the rest surfaced as VISIBLE typed "
            "cnf_unsupported lowering gaps — keeping the corpus 0-BILLABLE (0 new "
            "replay_bug/unknown). Regenerate with `uv run python "
            "-m lawvm.core.ctsf_gate --update-eu-baseline` (needs the EU Cellar "
            "Farchive) after a legitimate, reviewed corpus/projection change — a "
            "preregistered predict-then-compare event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_EU_JURISDICTION,
        "corpus_oracle_bases": list(REAL_ANCHOR_EU_ORACLE_BASES),
        "corpus_chains": [list(c) for c in REAL_ANCHOR_EU_CORPUS_CHAINS],
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "typed_skip_bucket_counts": typed_skip_bucket_counts or {},
        "residuals": residuals,
    }


def load_eu_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen EU typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_EU_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_eu_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen EU typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL EU corpus (``score_eu_real_corpus()``). Reads
    the EU Cellar Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_EU_BASELINE_PATH
    if residuals is None:
        score = score_eu_real_corpus_account()
        payload = _eu_baseline_payload(
            score.residuals,
            typed_skip_bucket_counts=score.typed_skip_bucket_counts,
        )
    else:
        payload = _eu_baseline_payload(residuals)
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def run_eu_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL EU corpus and diff it against the frozen EU baseline.

    Reads the EU Cellar Farchive (the EU corpus is scored via offline replay).
    Deterministic given the frozen corpus bytes. Returns the :class:`GateResult`; the
    fail-red enforcement lives in :func:`run_gate` / :func:`main` (which gate ALL four
    corpora).
    """
    current = score_eu_real_corpus()
    baseline = load_eu_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


# ---------------------------------------------------------------------------
# The REAL touch-relation corpus — NEW ZEALAND (task #205, FIFTH jurisdiction).
#
# The NZ analogue of EE/UK: ``nz_anchor_manifest`` replays NZ's dated PIT
# archived-version chain and emits typed ``TouchObservation``s whose verdict projects
# into the shared CTSF residual family. The gate scores THAT set against the frozen NZ
# baseline — the same fail-red mechanism as FI/EE/UK/EU. The frozen NZ corpus is
# curated to a preregistered residual set (all non-billable ``temporal_mismatch``
# coverage-lag observations; 0 billable NZ replay bugs).
#
# Scoring lives in ``nz_anchor_manifest`` (``score_nz_real_corpus``); the corpus
# membership + jurisdiction + baseline path are frozen constants there. The gate
# imports them so the wiring is a thin, uniform mirror of the EU section.
# ---------------------------------------------------------------------------

from lawvm.tools.nz_anchor_manifest import (  # noqa: E402
    GATE_NZ_BASELINE_PATH,
    REAL_ANCHOR_NZ_CORPUS_SIDS,  # noqa: F401 — re-exported for parity with EU constants
    REAL_ANCHOR_NZ_JURISDICTION,  # noqa: F401 — re-exported for parity
    nz_anchor_corpus_available,
    score_nz_real_corpus,
)


def load_nz_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen NZ typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_NZ_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def run_nz_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL NZ corpus and diff it against the frozen NZ baseline.

    Reads the NZ legislation Farchive (the NZ corpus is scored via dated-PIT replay).
    Deterministic given the frozen corpus bytes. Returns the :class:`GateResult`; the
    fail-red enforcement lives in :func:`run_gate` / :func:`main` (which gate ALL
    data-backed corpora).
    """
    current = score_nz_real_corpus()
    baseline = load_nz_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


def _nz_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — NEW ZEALAND (#183/#205). Frozen "
            "typed-residual set of the REAL NZ touch-relation anchor corpus "
            "(REAL_ANCHOR_NZ_CORPUS_SIDS), keyed {sid: {family: count}} with only "
            "non-zero families retained. A NZ anchor is one DATED archived consolidated "
            "snapshot (legislation.govt.nz publishes a dense dated PIT chain — the "
            "RICHEST anchor surface of any frontend); the chain replay carries a single "
            "evolving tree base->latest and each dated version is scored against its "
            "archived oracle. NZ chain replay is an EXPLICIT partial-coverage dry-run "
            "(replay_claims=False): a per-anchor oracle-vs-replay disagreement is "
            "COVERAGE LAG (a skipped/uncovered/pre-2007-baked op the oracle already "
            "reflects) unless NZ's authoritative op-LOCAL divergence detector convicts "
            "it (an oracle-present produced unit the on-or-after oracle contradicts). "
            "Coverage-lag anchors are commensurability-limited (oracle_suspect) so their "
            "divergences type to temporal_mismatch (non-billable); only an "
            "op-local-convicted, touched, persistent divergence is a replay_bug. The "
            "gate FAILs iff a NEW replay_bug/unknown residual appears vs this set; WARNs "
            "on a typed oracle/editorial/temporal move. This corpus is curated "
            "0-BILLABLE (the honest steady state); NZ acts whose chain replay surfaces a "
            "genuine op-local wrong-op are deliberately excluded (defects to fix, not to "
            "freeze). Regenerate with `uv run python -m lawvm.core.ctsf_gate "
            "--update-nz-baseline` (needs the NZ Farchive) after a legitimate, reviewed "
            "corpus/projection change — a preregistered predict-then-compare event, "
            "never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_NZ_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_NZ_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def write_nz_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen NZ typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL NZ corpus (``score_nz_real_corpus()``). Reads the
    NZ legislation Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_NZ_BASELINE_PATH
    payload = _nz_baseline_payload(
        residuals if residuals is not None else score_nz_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# The REAL touch-relation corpus — SWEDEN (task #205, SIXTH jurisdiction).
#
# The SE analogue of EE/UK: ``se_anchor_manifest`` replays SFS amendment windows and
# emits typed ``TouchObservation``s projected into the shared CTSF residual family. The
# gate scores THAT set against the frozen SE baseline (0-billable steady state). The SE
# manifest already owns its ``load_se_baseline`` / ``write_se_baseline`` (imported here
# for the gate's uniform report surface); the gate adds only the report + fold.
# ---------------------------------------------------------------------------

from lawvm.tools.se_anchor_manifest import (  # noqa: E402
    REAL_ANCHOR_SE_CORPUS_SIDS,  # noqa: F401 — re-exported for parity
    REAL_ANCHOR_SE_JURISDICTION,  # noqa: F401 — re-exported for parity
    GATE_SE_BASELINE_PATH,  # noqa: F401 — re-exported for parity
    load_se_baseline,
    score_se_real_corpus,  # noqa: F401 — re-exported for parity
    se_anchor_corpus_available,
    write_se_baseline,
)


def run_se_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL SE corpus and diff it against the frozen SE baseline.

    Reads the Sweden (SFS) Farchive. Deterministic given the frozen corpus bytes.
    Returns the shared :class:`GateResult`; fail-red enforcement lives in
    :func:`run_gate` / :func:`main`.
    """
    current = score_se_real_corpus()
    baseline = load_se_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


# ---------------------------------------------------------------------------
# The REAL touch-relation corpus — NORWAY (task #205, SEVENTH jurisdiction).
#
# The NO analogue of EE/UK: ``no_anchor_manifest`` replays Lovdata base→as_of windows
# and emits typed ``TouchObservation``s projected into the shared CTSF residual family
# (0-billable steady state). The NO manifest already owns its ``load_no_baseline`` /
# ``write_no_baseline`` AND a ``run_no_gate_report`` that returns the shared
# :class:`GateResult`; the gate re-exports them for a uniform report surface.
# ---------------------------------------------------------------------------

from lawvm.tools.no_anchor_manifest import (  # noqa: E402
    REAL_ANCHOR_NO_CORPUS_SIDS,  # noqa: F401 — re-exported for parity
    REAL_ANCHOR_NO_JURISDICTION,  # noqa: F401 — re-exported for parity
    GATE_NO_BASELINE_PATH,  # noqa: F401 — re-exported for parity
    load_no_baseline,  # noqa: F401 — re-exported for parity
    no_anchor_corpus_available,
    run_no_gate_report,
    score_no_real_corpus,  # noqa: F401 — re-exported for parity
    write_no_baseline,
)

# ---------------------------------------------------------------------------
# The REAL touch-relation corpus — UNITED STATES (task #205, EIGHTH + final
# jurisdiction).
#
# The US analogue of NO/EE/UK: ``us_anchor_manifest`` replays OLRC adjacent-edition
# USC windows and projects the per-window dry-run disposition partition into the shared
# CTSF residual family (0-billable steady state). The US manifest already owns its
# ``load_us_baseline`` / ``write_us_baseline`` AND ``score_us_real_corpus`` (unlike
# NZ/SE, which needed the gate to add their baseline I/O), so the gate imports them
# as-is and only adds ``run_us_gate_report`` (mirroring ``run_nz_gate_report``). US
# keys its baseline by ``corpus_windows`` (adjacent-edition windows), not ``corpus_sids``.
# ---------------------------------------------------------------------------

from lawvm.tools.us_anchor_manifest import (  # noqa: E402
    REAL_ANCHOR_US_CORPUS_WINDOWS,  # noqa: F401 — re-exported for parity
    REAL_ANCHOR_US_JURISDICTION,  # noqa: F401 — re-exported for parity
    GATE_US_BASELINE_PATH,  # noqa: F401 — re-exported for parity
    load_us_baseline,
    score_us_real_corpus,
    us_anchor_corpus_available,
    write_us_baseline,
)


def run_us_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL US corpus and diff it against the frozen US baseline.

    Reads the US federal Farchive (the US corpus is scored via per-window offline
    dry-run replay). Deterministic given the frozen corpus bytes. Returns the
    :class:`GateResult`; the fail-red enforcement lives in :func:`run_gate` /
    :func:`main` (which gate ALL data-backed corpora).
    """
    current = score_us_real_corpus()
    baseline = load_us_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


def ee_anchor_corpus_available() -> bool:
    """True iff the Riigi Teataja archive backing the EE real corpus is present.

    Scoring the EE #183 corpus re-derives the touch relation per anchor via EE
    replay, which reads the RT Farchive. When it is absent (a corpus-free CI
    checkout) the EE real-corpus tests SKIP; the gate's unit surface stays
    corpus-free and always runs.
    """
    try:
        from lawvm.estonia.fetch import open_rt_archive
        from lawvm.tools.ee_bench import _DEFAULT_DB

        if not _DEFAULT_DB.exists():
            return False
        archive = open_rt_archive(_DEFAULT_DB, readonly=True)
        archive.close()
        return True
    # An availability PROBE: any archive-open failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline).
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error
    except Exception:  # noqa: BLE001
        return False


@memoize_default_corpus
def score_ee_real_corpus(
    sids: Iterable[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Score the EE #183 touch-relation corpus into its typed-residual set.

    For each grupi_id, run the ``ee_anchor_manifest`` attribution engine over its
    published-terviktekst anchors and project each ``TouchObservation`` into its CTSF
    residual family (the shared ``_VERDICT_TO_FAMILY``). Returns the same diffable
    ``{sid: {family: count}}`` shape as :func:`score_real_corpus`, only non-zero
    families retained, a clean-but-scored statute present with an empty family map.
    Deterministic in sid order.

    Reads the RT Farchive (per-anchor EE replay). Deterministic given the frozen
    corpus bytes; NOT the wall-clock-free path — same as the FI real corpus.
    """
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.tools.ee_anchor_manifest import (
        _VERDICT_TO_FAMILY as _EE_VERDICT_TO_FAMILY,
        attribute_statute as ee_attribute_statute,
    )
    from lawvm.tools.ee_bench import _DEFAULT_DB

    corpus_sids = tuple(sids) if sids is not None else REAL_ANCHOR_EE_CORPUS_SIDS
    # Open ONE archive handle for the whole corpus so the ee_anchor_manifest
    # archive-wide index is built once (not re-scanned per statute) — a corpus sweep
    # is then a single archive pass + per-statute replay.
    archive = open_rt_archive(_DEFAULT_DB, readonly=True)
    try:
        out: dict[str, dict[str, int]] = {}
        for sid in corpus_sids:
            attr = ee_attribute_statute(sid, archive=archive)
            families: dict[str, int] = {}
            for obs in attr.observations:
                family = _EE_VERDICT_TO_FAMILY[obs.verdict]
                families[family] = families.get(family, 0) + 1
            out[sid] = {fam: n for fam, n in sorted(families.items()) if n}
        return dict(sorted(out.items()))
    finally:
        archive.close()


def real_anchor_corpus_available() -> bool:
    """True iff the Finlex corpus archive backing the real corpus is present.

    Scoring the real #183 corpus re-derives the touch relation per anchor via
    replay, which reads the Finlex archive. When it is absent (a corpus-free CI
    checkout) the real-corpus tests SKIP; the gate's unit surface (diff logic,
    synthetic corpus, baseline round-trip) is corpus-free and always runs.
    """
    try:
        from lawvm.finland.corpus import _archive_from_source, get_corpus

        return _archive_from_source(get_corpus()) is not None
    # An availability PROBE: any corpus-load failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline) — a deliberate
    # boolean signal, not a swallowed failure.
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error
    except Exception:  # noqa: BLE001
        return False


@memoize_default_corpus
def score_real_corpus(
    sids: Iterable[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Score the real #183 touch-relation corpus into its typed-residual set.

    For each statute id, run the ``fi_anchor_manifest`` attribution engine over its
    published-consolidation anchors and project each emitted ``TouchObservation``
    into its CTSF residual family (the engine's own ``_VERDICT_TO_FAMILY``). Returns
    the same diffable ``{sid: {family: count}}`` shape as :func:`score_corpus`, with
    only non-zero families retained and a clean-but-scored statute present with an
    empty family map (so the diff still sees the sid). Deterministic in sid order.

    Reads the Finlex corpus archive (per-anchor replay). Deterministic given the
    frozen corpus bytes; NOT the wall-clock-free path — see the module docstring.
    """
    from lawvm.tools.fi_anchor_manifest import _VERDICT_TO_FAMILY, attribute_statute

    corpus_sids = tuple(sids) if sids is not None else REAL_ANCHOR_CORPUS_SIDS
    out: dict[str, dict[str, int]] = {}
    for sid in corpus_sids:
        attr = attribute_statute(sid)
        families: dict[str, int] = {}
        for obs in attr.observations:
            family = _VERDICT_TO_FAMILY[obs.verdict]
            families[family] = families.get(family, 0) + 1
        # Retain only non-zero families (mirrors residual_set); a scored-but-clean
        # statute lands with an empty map so the diff still carries the sid.
        out[sid] = {fam: n for fam, n in sorted(families.items()) if n}
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# The typed residual set — the diffable object. A per-(sid, family) count multiset
# over the corpus; the frozen baseline is a snapshot of it.
# ---------------------------------------------------------------------------


def residual_set(reports: Iterable[CTSFResidualReport]) -> dict[str, dict[str, int]]:
    """Project a corpus's reports into the diffable typed-residual set.

    ``{sid: {family: count}}`` — only NON-ZERO family counts are retained (a sid
    with a fully-clean verdict is present with an empty family map, so the diff
    still sees the sid, but noise is not carried). Deterministic in sid order.
    """
    out: dict[str, dict[str, int]] = {}
    for rep in reports:
        families = {
            family: rep.verdict.get(family, 0)
            for family in RESIDUAL_VERDICT_FAMILIES
            if rep.verdict.get(family, 0)
        }
        out[rep.sid] = families
    return dict(sorted(out.items()))


def score_corpus(
    corpus: Iterable[GateAnchor] | None = None,
) -> dict[str, dict[str, int]]:
    """Score the (frozen) corpus into its typed-residual set. Pure + deterministic."""
    anchors = tuple(corpus) if corpus is not None else frozen_gate_corpus()
    return residual_set(a.report() for a in anchors)


# ---------------------------------------------------------------------------
# The gate — a pure function of (current residual set, frozen baseline).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateResult:
    """The residual-set-diff gate verdict for a corpus vs the frozen baseline.

    ``verdict`` is FAIL iff ``new_billable`` is non-empty (a new ``replay_bug`` or
    ``unknown`` residual appeared — the ``has_replay_bug_or_unknown`` predicate over
    the diff); WARN iff the set MOVED but only in typed non-billable families; PASS
    iff the set equals the baseline exactly. In the PRIMARY gate a FAIL flips the exit
    code red (:func:`run_gate` / :func:`main`); WARN/PASS exit 0.
    """

    verdict: GateVerdict
    new_billable: tuple[str, ...]
    typed_moves: tuple[str, ...]
    resolved: tuple[str, ...]
    current: dict[str, dict[str, int]]
    baseline: dict[str, dict[str, int]]

    @property
    def failed(self) -> bool:
        return self.verdict == "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_version": GATE_VERSION,
            "verdict": self.verdict,
            "new_billable": list(self.new_billable),
            "typed_moves": list(self.typed_moves),
            "resolved": list(self.resolved),
            "current": self.current,
            "baseline": self.baseline,
        }


def _diff_lines(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> tuple[list[str], list[str], list[str]]:
    """Return (new_billable, typed_moves, resolved) diff lines.

    * ``new_billable``: a ``(sid, family)`` whose current count EXCEEDS the baseline
      count AND ``family`` is a FAIL family — a new billable residual appeared.
    * ``typed_moves``: any other ``(sid, family)`` whose count rose vs baseline (a
      typed non-billable move — WARN telemetry).
    * ``resolved``: a ``(sid, family)`` whose count FELL vs baseline (a residual
      cleared — reported so an unexpected drop is visible, never silently eaten).
    """
    new_billable: list[str] = []
    typed_moves: list[str] = []
    resolved: list[str] = []

    all_sids = sorted(set(current) | set(baseline))
    for sid in all_sids:
        cur_fam = current.get(sid, {})
        base_fam = baseline.get(sid, {})
        families = sorted(set(cur_fam) | set(base_fam))
        for family in families:
            cur = cur_fam.get(family, 0)
            base = base_fam.get(family, 0)
            if cur > base:
                line = f"{sid}:{family} {base}->{cur}"
                if family in FAIL_FAMILIES:
                    new_billable.append(line)
                else:
                    typed_moves.append(line)
            elif cur < base:
                resolved.append(f"{sid}:{family} {base}->{cur}")
    return new_billable, typed_moves, resolved


def residual_set_diff_gate(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> GateResult:
    """Diff the current typed-residual set against the frozen baseline → verdict.

    Pure function. FAIL iff a NEW ``replay_bug``/``unknown`` residual appeared
    (``has_replay_bug_or_unknown`` over the diff); WARN iff the set moved only in
    typed non-billable families (incl. a resolved residual); PASS iff unchanged.
    """
    new_billable, typed_moves, resolved = _diff_lines(current, baseline)
    if new_billable:
        verdict: GateVerdict = "FAIL"
    elif typed_moves or resolved:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return GateResult(
        verdict=verdict,
        new_billable=tuple(new_billable),
        typed_moves=tuple(typed_moves),
        resolved=tuple(resolved),
        current=current,
        baseline=baseline,
    )


# ---------------------------------------------------------------------------
# Frozen baseline artifact — round-trippable JSON.
# ---------------------------------------------------------------------------


def _baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF Phase-3 residual-set-diff gate baseline (#186/#183). Frozen typed-"
            "residual set of the REAL #183 FI touch-relation anchor corpus "
            "(REAL_ANCHOR_CORPUS_SIDS), keyed {sid: {family: count}} with only "
            "non-zero families retained. The gate FAILs iff a NEW replay_bug/unknown "
            "residual appears vs this set; WARNs on a typed oracle/editorial/state-"
            "index/temporal move. The standing replay_bug/unknown residuals here are "
            "REAL defects the honest metric exposes (acknowledged current state, the "
            "starting line — the gate catches NEW ones, it does not zero these out). "
            "Regenerate with `uv run python -m lawvm.core.ctsf_gate "
            "--update-baseline` (needs the Finlex corpus) after a legitimate, "
            "reviewed corpus/projection change — a preregistered predict-then-compare "
            "event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    # Normalize to plain int counts (JSON round-trip yields ints already; defensive).
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL #183 corpus (``score_real_corpus()``) — the
    honest production baseline. Reads the Finlex corpus; pass ``residuals`` to write
    a precomputed set (e.g. in a corpus-free context).
    """
    p = path if path is not None else _repo_root() / GATE_BASELINE_PATH
    payload = _baseline_payload(
        residuals if residuals is not None else score_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# Estonia baseline artifact (task #205) — a sibling of the FI one, same shape.
# ---------------------------------------------------------------------------


def _ee_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — ESTONIA (#183/#205). Frozen typed-"
            "residual set of the REAL EE touch-relation anchor corpus "
            "(REAL_ANCHOR_EE_CORPUS_SIDS), keyed {sid: {family: count}} with only "
            "non-zero families retained. The gate FAILs iff a NEW replay_bug/unknown "
            "residual appears vs this set; WARNs on a typed oracle/editorial/temporal "
            "move. This corpus is curated 0-BILLABLE (the honest steady state); deep "
            "EE chains that surface genuine replay bugs are deliberately excluded "
            "(they are defects to fix, not to freeze). Regenerate with `uv run python "
            "-m lawvm.core.ctsf_gate --update-ee-baseline` (needs the RT Farchive) "
            "after a legitimate, reviewed corpus/projection change — a preregistered "
            "predict-then-compare event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_EE_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_EE_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_ee_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen EE typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_EE_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_ee_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen EE typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL EE corpus (``score_ee_real_corpus()``). Reads
    the RT Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_EE_BASELINE_PATH
    payload = _ee_baseline_payload(
        residuals if residuals is not None else score_ee_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def run_ee_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL EE corpus and diff it against the frozen EE baseline.

    Reads the RT Farchive (the EE corpus is scored via replay). Deterministic given
    the frozen corpus bytes. Returns the :class:`GateResult`; the fail-red
    enforcement lives in :func:`run_gate` / :func:`main` (which gate BOTH corpora).
    """
    current = score_ee_real_corpus()
    baseline = load_ee_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


# ---------------------------------------------------------------------------
# United Kingdom corpus (task #205) — the third jurisdiction, same shape as EE.
# ---------------------------------------------------------------------------


def uk_anchor_corpus_available() -> bool:
    """True iff the legislation.gov.uk Farchive backing the UK real corpus is present.

    Scoring the UK #205 corpus re-derives the touch relation per act via UK
    enacted→current replay, which reads the UK Farchive. When it is absent (a
    corpus-free CI checkout) the UK real-corpus tests SKIP; the gate's unit surface
    stays corpus-free and always runs.
    """
    try:
        from lawvm.tools.uk_anchor_manifest import _default_db

        return _default_db().exists()
    # An availability PROBE: any archive-open failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline).
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error
    except Exception:  # noqa: BLE001
        return False


@memoize_default_corpus
def score_uk_real_corpus(
    sids: Iterable[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Score the UK #205 touch-relation corpus into its typed-residual set.

    For each act, run the ``uk_anchor_manifest`` attribution engine over its
    enacted→current replay window and project each ``TouchObservation`` into its CTSF
    residual family (the shared ``_VERDICT_TO_FAMILY``). Returns the same diffable
    ``{sid: {family: count}}`` shape as :func:`score_real_corpus`, only non-zero
    families retained, a clean-but-scored act present with an empty family map.
    Deterministic in sid order.

    Reads the UK Farchive (per-act UK replay). Deterministic given the frozen corpus
    bytes; NOT the wall-clock-free path — same as the FI/EE real corpora.
    """
    from farchive import Farchive

    from lawvm.tools.uk_anchor_manifest import (
        _VERDICT_TO_FAMILY as _UK_VERDICT_TO_FAMILY,
        _default_db as _uk_default_db,
        attribute_statute as uk_attribute_statute,
    )

    corpus_sids = tuple(sids) if sids is not None else REAL_ANCHOR_UK_CORPUS_SIDS
    # Open ONE archive handle for the whole corpus so each act's replay reuses it.
    archive = Farchive(str(_uk_default_db()))
    try:
        out: dict[str, dict[str, int]] = {}
        for sid in corpus_sids:
            attr = uk_attribute_statute(sid, archive=archive)
            families: dict[str, int] = {}
            for obs in attr.observations:
                family = _UK_VERDICT_TO_FAMILY[obs.verdict]
                families[family] = families.get(family, 0) + 1
            out[sid] = {fam: n for fam, n in sorted(families.items()) if n}
        return dict(sorted(out.items()))
    finally:
        archive.close()


def _uk_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — UNITED KINGDOM (#183/#205). Frozen "
            "typed-residual set of the REAL UK touch-relation anchor corpus "
            "(REAL_ANCHOR_UK_CORPUS_SIDS), keyed {sid: {family: count}} with only "
            "non-zero families retained. A UK anchor is the enacted→current replay "
            "window (UK has no dated PIT chain — verified 0 dated PIT locators). The "
            "gate FAILs iff a NEW replay_bug/unknown residual appears vs this set; "
            "WARNs on a typed oracle/editorial/temporal move. This corpus is curated "
            "0-BILLABLE (the honest steady state); UK acts whose enacted→current "
            "replay surfaces genuine billable residuals are deliberately excluded "
            "(they are defects to fix, not to freeze). Regenerate with `uv run python "
            "-m lawvm.core.ctsf_gate --update-uk-baseline` (needs the UK Farchive) "
            "after a legitimate, reviewed corpus/projection change — a preregistered "
            "predict-then-compare event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_UK_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_UK_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_uk_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen UK typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_UK_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_uk_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen UK typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL UK corpus (``score_uk_real_corpus()``). Reads
    the UK Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_UK_BASELINE_PATH
    payload = _uk_baseline_payload(
        residuals if residuals is not None else score_uk_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def run_uk_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL UK corpus and diff it against the frozen UK baseline.

    Reads the UK Farchive (the UK corpus is scored via replay). Deterministic given
    the frozen corpus bytes. Returns the :class:`GateResult`; the fail-red enforcement
    lives in :func:`run_gate` / :func:`main` (which gate ALL three corpora).
    """
    current = score_uk_real_corpus()
    baseline = load_uk_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


def _repo_root() -> Path:
    # src/lawvm/core/ctsf_gate.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# The primary-gate surface — scores the real #183 corpus, diffs it against the
# frozen baseline, and (in the callable/CLI entries) flips the exit code red on a
# new billable residual. Data-aware: gates where the corpus is present, skips clean
# where absent.
# ---------------------------------------------------------------------------


def run_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the REAL #183 corpus and diff it against the frozen baseline.

    Reads the Finlex corpus (the real corpus is scored via replay). Deterministic
    given the frozen corpus bytes. Returns the :class:`GateResult`; the fail-red
    enforcement lives in :func:`run_gate` / :func:`main`.
    """
    current = score_real_corpus()
    baseline = load_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


def emit_verdict_signals(result: GateResult) -> None:
    """Make the verdict OBSERVABLE (never silent): log FAIL at ERROR, WARN at WARNING.

    A WARN — a move only in typed non-billable families (or a resolved residual) — is
    surfaced as telemetry rather than swallowed; a FAIL (a new billable residual) is
    logged at ERROR alongside flipping the exit code red.
    """
    if result.verdict == "FAIL":
        _LOG.error(
            "CTSF gate FAIL: new billable (replay_bug/unknown) residual(s) vs "
            "baseline: %s",
            ", ".join(result.new_billable),
        )
    elif result.verdict == "WARN":
        _LOG.warning(
            "CTSF gate WARN: typed non-billable residual move(s) (telemetry, not a "
            "regression): moves=%s resolved=%s",
            ", ".join(result.typed_moves) or "-",
            ", ".join(result.resolved) or "-",
        )


def run_gate(baseline_path: Path | None = None) -> int:
    """PRIMARY callable gate entry → exit code.

    Data-aware, MULTI-JURISDICTION: gates the FI #183 corpus, the EE #205 corpus, the
    UK #205 corpus, the EU #204 corpus, and the NZ / SE / NO #205 corpora — ALL
    data-backed jurisdictions. For each jurisdiction whose archive is
    PRESENT, score its real
    corpus, diff against the frozen baseline, emit the verdict signals, and fail red
    (1) iff a NEW billable (``replay_bug``/``unknown``) residual appeared. A
    jurisdiction whose archive is ABSENT SKIPS cleanly (its corpus is derived via
    replay), so data-less CI is never failed. The exit code is NONZERO iff ANY present
    jurisdiction FAILs.

    ``baseline_path`` overrides the FI baseline only (kept for back-compat with the FI
    callers/tests); the EE / UK baselines are always the committed sibling artifacts.
    """
    rc = 0
    if real_anchor_corpus_available():
        result = run_gate_report(baseline_path)
        emit_verdict_signals(result)
        if result.failed:
            rc = 1
    if ee_anchor_corpus_available():
        ee_result = run_ee_gate_report()
        emit_verdict_signals(ee_result)
        if ee_result.failed:
            rc = 1
    if uk_anchor_corpus_available():
        uk_result = run_uk_gate_report()
        emit_verdict_signals(uk_result)
        if uk_result.failed:
            rc = 1
    if eu_anchor_corpus_available():
        eu_result = run_eu_gate_report()
        emit_verdict_signals(eu_result)
        if eu_result.failed:
            rc = 1
    if nz_anchor_corpus_available():
        nz_result = run_nz_gate_report()
        emit_verdict_signals(nz_result)
        if nz_result.failed:
            rc = 1
    if se_anchor_corpus_available():
        se_result = run_se_gate_report()
        emit_verdict_signals(se_result)
        if se_result.failed:
            rc = 1
    if no_anchor_corpus_available():
        no_result = run_no_gate_report()
        emit_verdict_signals(no_result)
        if no_result.failed:
            rc = 1
    if us_anchor_corpus_available():
        us_result = run_us_gate_report()
        emit_verdict_signals(us_result)
        if us_result.failed:
            rc = 1
    return rc


def format_report(
    result: GateResult,
    corpus_label: str = "the REAL #183 FI touch-relation anchor corpus",
) -> str:
    """Human-readable rendering of the PRIMARY gate verdict.

    Labels the mode as PRIMARY so no reader mistakes it for telemetry: a FAIL flips
    the exit code red. The legacy scalar bench-regression guard is demoted to
    telemetry (an operator diagnostic, no longer the correctness authority).
    ``corpus_label`` names which jurisdiction's corpus this verdict is over (defaults
    to FI for back-compat; the EE fold passes its own label).
    """
    cur_total = sum(sum(f.values()) for f in result.current.values())
    base_total = sum(sum(f.values()) for f in result.baseline.values())
    lines = [
        "CTSF residual-set-diff gate (Phase 3) — PRIMARY GATE (load-bearing)",
        f"  over {corpus_label}",
        "  (legacy scalar bench gate DEMOTED to telemetry; this verdict is the "
        "correctness authority — a new billable FAILs red)",
        f"  verdict: {result.verdict}",
        f"  corpus residuals: {cur_total}   baseline residuals: {base_total}",
    ]
    if result.new_billable:
        lines.append("  NEW billable (replay_bug/unknown) residuals:")
        lines += [f"    {line}" for line in result.new_billable]
    if result.typed_moves:
        lines.append("  typed non-billable moves (WARN telemetry):")
        lines += [f"    {line}" for line in result.typed_moves]
    if result.resolved:
        lines.append("  resolved residuals (fell vs baseline):")
        lines += [f"    {line}" for line in result.resolved]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: the PRIMARY gate — exit NONZERO on a new billable residual.

    ``--update-baseline`` rewrites the frozen baseline from the current corpus (a
    preregistered predict-then-compare event; always exits 0). Otherwise:

    * corpus PRESENT → score the real #183 corpus, diff against the frozen baseline,
      print/log the verdict, and return NONZERO (1) iff a NEW billable
      (``replay_bug``/``unknown``) residual appeared (FAIL). WARN/PASS return 0.
    * corpus ABSENT → the real corpus cannot be scored (derived via replay), so the
      gate SKIPS cleanly: report the frozen baseline as the pinned state and return 0.
      Data-less CI is never failed by this gate.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the frozen CTSF gate residual baseline from the corpus.",
    )
    parser.add_argument(
        "--update-ee-baseline",
        action="store_true",
        help="Rewrite the frozen EE (#205) CTSF gate residual baseline from the "
        "Riigi Teataja corpus.",
    )
    parser.add_argument(
        "--update-uk-baseline",
        action="store_true",
        help="Rewrite the frozen UK (#205) CTSF gate residual baseline from the "
        "legislation.gov.uk corpus.",
    )
    parser.add_argument(
        "--update-eu-baseline",
        action="store_true",
        help="Rewrite the frozen EU (#204) CTSF gate residual baseline from the "
        "EU Cellar corpus.",
    )
    parser.add_argument(
        "--update-nz-baseline",
        action="store_true",
        help="Rewrite the frozen NZ (#205) CTSF gate residual baseline from the "
        "legislation.govt.nz corpus.",
    )
    parser.add_argument(
        "--update-se-baseline",
        action="store_true",
        help="Rewrite the frozen SE (#205) CTSF gate residual baseline from the "
        "Sweden (SFS) corpus.",
    )
    parser.add_argument(
        "--update-no-baseline",
        action="store_true",
        help="Rewrite the frozen NO (#205) CTSF gate residual baseline from the "
        "Norway (Lovdata) corpus.",
    )
    parser.add_argument(
        "--update-us-baseline",
        action="store_true",
        help="Rewrite the frozen US (#205) CTSF gate residual baseline from the "
        "US federal (OLRC USC) corpus.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the gate result as JSON instead of the human report.",
    )
    args = parser.parse_args(argv)

    if args.update_uk_baseline:
        if not uk_anchor_corpus_available():
            print(
                "Cannot regenerate the UK #205 baseline: the legislation.gov.uk "
                "Farchive is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with "
                "the UK corpus and retry."
            )
            return 0
        out = write_uk_baseline()
        print(f"Wrote CTSF gate UK residual baseline: {out}")
        return 0

    if args.update_eu_baseline:
        if not eu_anchor_corpus_available():
            print(
                "Cannot regenerate the EU #204 baseline: the EU Cellar Farchive is "
                "absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with the EU corpus "
                "and retry."
            )
            return 0
        out = write_eu_baseline()
        print(f"Wrote CTSF gate EU residual baseline: {out}")
        return 0

    if args.update_nz_baseline:
        if not nz_anchor_corpus_available():
            print(
                "Cannot regenerate the NZ #205 baseline: the legislation.govt.nz "
                "Farchive is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with "
                "the NZ corpus and retry."
            )
            return 0
        out = write_nz_baseline()
        print(f"Wrote CTSF gate NZ residual baseline: {out}")
        return 0

    if args.update_se_baseline:
        if not se_anchor_corpus_available():
            print(
                "Cannot regenerate the SE #205 baseline: the Sweden (SFS) Farchive is "
                "absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with the SE corpus "
                "and retry."
            )
            return 0
        out = write_se_baseline()
        print(f"Wrote CTSF gate SE residual baseline: {out}")
        return 0

    if args.update_no_baseline:
        if not no_anchor_corpus_available():
            print(
                "Cannot regenerate the NO #205 baseline: the Norway (Lovdata) Farchive "
                "is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with the NO "
                "corpus and retry."
            )
            return 0
        out = write_no_baseline()
        print(f"Wrote CTSF gate NO residual baseline: {out}")
        return 0

    if args.update_us_baseline:
        if not us_anchor_corpus_available():
            print(
                "Cannot regenerate the US #205 baseline: the US federal (OLRC USC) "
                "Farchive is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with "
                "the US corpus and retry."
            )
            return 0
        out = write_us_baseline()
        print(f"Wrote CTSF gate US residual baseline: {out}")
        return 0

    if args.update_ee_baseline:
        if not ee_anchor_corpus_available():
            print(
                "Cannot regenerate the EE #205 baseline: the Riigi Teataja Farchive "
                "is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with the EE "
                "corpus and retry."
            )
            return 0
        out = write_ee_baseline()
        print(f"Wrote CTSF gate EE residual baseline: {out}")
        return 0

    if args.update_baseline:
        if not real_anchor_corpus_available():
            print(
                "Cannot regenerate the real #183 baseline: the Finlex corpus archive "
                "is absent. Set LAWVM_CANONICAL_DATA_ROOT to a checkout with the FI "
                "corpus and retry."
            )
            return 0
        out = write_baseline()
        print(f"Wrote CTSF gate residual baseline: {out}")
        return 0

    if not real_anchor_corpus_available():
        # Corpus-free context: the real corpus cannot be scored (it is derived via
        # replay). Report the frozen baseline as the pinned state and exit 0 — the
        # PRIMARY gate SKIPS cleanly when the archive is absent, so data-less CI is
        # never failed. The fail-red enforcement runs where the corpus is present.
        # (EE mirrors this: an absent RT archive skips its lane too.)
        baseline = load_baseline()
        frozen = residual_set_diff_gate(baseline, baseline)
        if args.json:
            payload = frozen.to_dict()
            payload["note"] = "corpus_absent: skipped clean, reporting frozen baseline"
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(format_report(frozen))
            print(
                "  NOTE: Finlex corpus absent — PRIMARY gate SKIPPED clean; reporting "
                "the frozen baseline as the pinned state (fail-red runs where the "
                "corpus is present)."
            )
        # Even with FI absent, the EE / UK / EU corpora may be present — gate
        # independently.
        rc = _fold_ee_gate_into_rc(0, json_mode=args.json)
        rc = _fold_uk_gate_into_rc(rc, json_mode=args.json)
        rc = _fold_eu_gate_into_rc(rc, json_mode=args.json)
        rc = _fold_nz_gate_into_rc(rc, json_mode=args.json)
        rc = _fold_se_gate_into_rc(rc, json_mode=args.json)
        rc = _fold_no_gate_into_rc(rc, json_mode=args.json)
        return _fold_us_gate_into_rc(rc, json_mode=args.json)

    result = run_gate_report()
    emit_verdict_signals(result)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(result))
    # PRIMARY GATE: a new billable residual (FAIL) flips the exit code red; WARN/PASS
    # exit 0. Fold the EE + UK jurisdictions' verdicts into the same exit code.
    rc = 1 if result.failed else 0
    rc = _fold_ee_gate_into_rc(rc, json_mode=args.json)
    rc = _fold_uk_gate_into_rc(rc, json_mode=args.json)
    rc = _fold_eu_gate_into_rc(rc, json_mode=args.json)
    rc = _fold_nz_gate_into_rc(rc, json_mode=args.json)
    rc = _fold_se_gate_into_rc(rc, json_mode=args.json)
    rc = _fold_no_gate_into_rc(rc, json_mode=args.json)
    return _fold_us_gate_into_rc(rc, json_mode=args.json)


def _fold_ee_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the EE (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent RT archive SKIPS the EE lane cleanly (returns ``rc``
    unchanged). When present, score the EE corpus, log its verdict, and raise ``rc``
    to 1 iff the EE gate FAILs (a new EE billable). Keeps FI and EE independent —
    either jurisdiction FAILing flips CI red.

    In HUMAN mode the EE verdict is printed as its own labelled section. In JSON mode
    it is NOT printed as a second blob (that would break the single-object stdout
    contract the FI JSON callers/tests rely on) — the verdict is still logged via
    ``emit_verdict_signals`` and folded into the exit code; the machine-readable EE
    result is available directly via :func:`run_ee_gate_report`.
    """
    if not ee_anchor_corpus_available():
        return rc
    ee_result = run_ee_gate_report()
    emit_verdict_signals(ee_result)
    if not json_mode:
        print("\n--- ESTONIA (#205) corpus ---")
        print(
            format_report(
                ee_result,
                corpus_label="the REAL #205 EE touch-relation anchor corpus",
            )
        )
    return 1 if ee_result.failed else rc


def _fold_uk_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the UK (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent UK Farchive SKIPS the UK lane cleanly (returns ``rc``
    unchanged). When present, score the UK corpus, log its verdict, and raise ``rc``
    to 1 iff the UK gate FAILs (a new UK billable). Keeps FI/EE/UK independent — any
    jurisdiction FAILing flips CI red.

    In HUMAN mode the UK verdict is printed as its own labelled section. In JSON mode
    it is NOT printed as a second blob (that would break the single-object stdout
    contract the FI JSON callers/tests rely on) — the verdict is still logged via
    ``emit_verdict_signals`` and folded into the exit code; the machine-readable UK
    result is available directly via :func:`run_uk_gate_report`.
    """
    if not uk_anchor_corpus_available():
        return rc
    uk_result = run_uk_gate_report()
    emit_verdict_signals(uk_result)
    if not json_mode:
        print("\n--- UNITED KINGDOM (#205) corpus ---")
        print(
            format_report(
                uk_result,
                corpus_label="the REAL #205 UK touch-relation anchor corpus",
            )
        )
    return 1 if uk_result.failed else rc


def _fold_eu_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the EU (#204) corpus and fold its verdict into the process exit code.

    Data-aware: an absent EU Cellar Farchive SKIPS the EU lane cleanly (returns ``rc``
    unchanged). When present, score the EU corpus, log its verdict, and raise ``rc`` to
    1 iff the EU gate FAILs (a new EU billable — an apply RAISE or a conservation
    violation). Keeps FI/EE/UK/EU independent — any jurisdiction FAILing flips CI red.

    In HUMAN mode the EU verdict is printed as its own labelled section. In JSON mode it
    is NOT printed as a second blob (that would break the single-object stdout contract
    the FI JSON callers/tests rely on) — the verdict is still logged via
    ``emit_verdict_signals`` and folded into the exit code; the machine-readable EU
    result is available directly via :func:`run_eu_gate_report`.
    """
    if not eu_anchor_corpus_available():
        return rc
    eu_result = run_eu_gate_report()
    emit_verdict_signals(eu_result)
    if not json_mode:
        print("\n--- EUROPEAN UNION (#204/#221) corpus ---")
        print(
            format_report(
                eu_result,
                corpus_label=(
                    "the REAL #221 EU oracle-touch corpus "
                    "(+ #204 conserved-apply fallback)"
                ),
            )
        )
    return 1 if eu_result.failed else rc


def _fold_nz_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the NZ (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent NZ legislation Farchive SKIPS the NZ lane cleanly (returns
    ``rc`` unchanged). When present, score the NZ corpus, log its verdict, and raise
    ``rc`` to 1 iff the NZ gate FAILs (a new NZ billable). Keeps every jurisdiction
    independent — any one FAILing flips CI red. Human mode prints a labelled section;
    JSON mode logs the verdict + folds the exit code without a second stdout blob.
    """
    if not nz_anchor_corpus_available():
        return rc
    nz_result = run_nz_gate_report()
    emit_verdict_signals(nz_result)
    if not json_mode:
        print("\n--- NEW ZEALAND (#205) corpus ---")
        print(
            format_report(
                nz_result,
                corpus_label="the REAL #205 NZ touch-relation anchor corpus",
            )
        )
    return 1 if nz_result.failed else rc


def _fold_se_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the SE (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent Sweden Farchive SKIPS the SE lane cleanly. When present,
    score the SE corpus, log its verdict, and raise ``rc`` to 1 iff the SE gate FAILs.
    Human mode prints a labelled section; JSON mode logs + folds without a second blob.
    """
    if not se_anchor_corpus_available():
        return rc
    se_result = run_se_gate_report()
    emit_verdict_signals(se_result)
    if not json_mode:
        print("\n--- SWEDEN (#205) corpus ---")
        print(
            format_report(
                se_result,
                corpus_label="the REAL #205 SE touch-relation anchor corpus",
            )
        )
    return 1 if se_result.failed else rc


def _fold_no_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the NO (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent Norway Farchive SKIPS the NO lane cleanly. When present,
    score the NO corpus, log its verdict, and raise ``rc`` to 1 iff the NO gate FAILs.
    Human mode prints a labelled section; JSON mode logs + folds without a second blob.
    """
    if not no_anchor_corpus_available():
        return rc
    no_result = run_no_gate_report()
    emit_verdict_signals(no_result)
    if not json_mode:
        print("\n--- NORWAY (#205) corpus ---")
        print(
            format_report(
                no_result,
                corpus_label="the REAL #205 NO touch-relation anchor corpus",
            )
        )
    return 1 if no_result.failed else rc


def _fold_us_gate_into_rc(rc: int, *, json_mode: bool) -> int:
    """Gate the US (#205) corpus and fold its verdict into the process exit code.

    Data-aware: an absent US federal Farchive SKIPS the US lane cleanly. When present,
    score the US corpus, log its verdict, and raise ``rc`` to 1 iff the US gate FAILs.
    Human mode prints a labelled section; JSON mode logs + folds without a second blob.
    This is the EIGHTH and final CTSF jurisdiction — its fold completes the honest metric.
    """
    if not us_anchor_corpus_available():
        return rc
    us_result = run_us_gate_report()
    emit_verdict_signals(us_result)
    if not json_mode:
        print("\n--- UNITED STATES (#205) corpus ---")
        print(
            format_report(
                us_result,
                corpus_label="the REAL #205 US touch-relation anchor corpus",
            )
        )
    return 1 if us_result.failed else rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
