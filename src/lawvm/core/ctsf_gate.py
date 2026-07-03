"""CTSF residual-set-diff GATE — task #186 (CTSF Phase 3), PARALLEL / REPORT MODE.

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

STAGED / ADDITIVE DISCIPLINE (Phase 3, the deliberate migration): this gate runs
in **report mode** — its verdict is computed, reported, and TESTED, but it does NOT
flip CI red and it does NOT touch the legacy scalar bench gate's pass/fail
semantics. The legacy scalar remains the sole CI gate for now; this is the parallel
surface that will BECOME the primary gate in the follow-up flip. Computing this
gate leaves default bench output byte-identical (it runs over CTSF/STATE_INDEX
objects and a frozen in-code corpus only; no bench/replay/scoring path is touched).

Determinism: the gate is a PURE function of ``(frozen anchors, replay projection,
frozen baseline)``. The corpus is an explicit, frozen in-code set of anchor pairs
(``frozen_gate_corpus``); there is no wall-clock, randomness, or filesystem/network
read in the verdict path. Same corpus + same baseline ⇒ same verdict, byte-stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

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
    iff the set equals the baseline exactly. In Phase-3 report mode the verdict is
    reported, not enforced.
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
            "CTSF Phase-3 residual-set-diff gate baseline (#186). Frozen typed-"
            "residual set of the in-code frozen_gate_corpus, keyed {sid: {family: "
            "count}} with only non-zero families retained. The gate FAILs iff a NEW "
            "replay_bug/unknown residual appears vs this set; WARNs on a typed "
            "oracle/editorial/state-index/temporal move. Regenerate with `uv run "
            "python -m lawvm.core.ctsf_gate --update-baseline` after a legitimate, "
            "reviewed change to the frozen corpus or the projection."
        ),
        "gate_version": GATE_VERSION,
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
    """Write the frozen typed-residual baseline. Regeneration entrypoint."""
    p = path if path is not None else _repo_root() / GATE_BASELINE_PATH
    payload = _baseline_payload(
        residuals if residuals is not None else score_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def _repo_root() -> Path:
    # src/lawvm/core/ctsf_gate.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# The report-mode surface — computes both the legacy-scalar CONTEXT note and the
# CTSF gate verdict, and REPORTS them. Wired into the CLI (making this module and
# ctsf_residual_report production-reachable / LIVE) but NOT flipping any exit code.
# ---------------------------------------------------------------------------


def run_gate_report(baseline_path: Path | None = None) -> GateResult:
    """Score the frozen corpus and diff it against the frozen baseline. Pure."""
    current = score_corpus()
    baseline = load_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


def format_report(result: GateResult) -> str:
    """Human-readable REPORT-MODE rendering of the parallel gate verdict.

    Explicitly labels the mode as PARALLEL / REPORT so no reader mistakes it for the
    active CI gate: the legacy scalar bench gate remains the sole gate; this verdict
    is telemetry until the deliberate flip.
    """
    lines = [
        "CTSF residual-set-diff gate (Phase 3) — PARALLEL / REPORT MODE",
        "  (legacy scalar bench gate UNCHANGED; this verdict is not yet enforced)",
        f"  verdict: {result.verdict}",
        f"  corpus residuals: {sum(sum(f.values()) for f in result.current.values())}"
        f"  baseline residuals: {sum(sum(f.values()) for f in result.baseline.values())}",
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
    """CLI entrypoint: report the parallel gate verdict (report mode, exit 0).

    ``--update-baseline`` rewrites the frozen baseline from the current corpus.
    Otherwise it prints the parallel verdict and ALWAYS returns 0 — Phase-3 report
    mode never flips CI red on the CTSF verdict.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the frozen CTSF gate residual baseline from the corpus.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the gate result as JSON instead of the human report.",
    )
    args = parser.parse_args(argv)

    if args.update_baseline:
        out = write_baseline()
        print(f"Wrote CTSF gate residual baseline: {out}")
        return 0

    result = run_gate_report()
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(result))
    # REPORT MODE: the CTSF verdict is reported, never enforced. Always exit 0.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
