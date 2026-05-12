"""lawvm bisect — find which amendment damages a statute's replay score.

Algorithm:
  1. Get the ordered amendment chain (same ordering as replay_xml).
  2. Apply amendments cumulatively to a fresh base statute.
  3. After each amendment, score the intermediate state against the FINAL
     consolidated oracle (consolidated corpus — no PIT oracles exist).
  4. Report amendments where the score drops (damage signal).

Oracle note: We compare every intermediate state against the FINAL oracle.
Score should generally increase as amendments accumulate toward the final
state. A DROP after amendment N means that amendment damaged something
(destructive replacement, wrong target, cascading corruption, etc.). This
is noisy early in the chain but reliably catches the main failure mode.

Score at each step: apply the same post-processing as replay_xml (strip
omissions, hoist sections, normalize text nodes) to a snapshot copy, then
Levenshtein.ratio on cleaned text vs oracle. The main tree is NOT
post-processed between steps so future amendments can reference its state.

Usage (via lawvm CLI):
    lawvm bisect 2006/1299
    lawvm bisect 2006/1299 --verbose
    lawvm bisect 2006/1299 --mode legal_pit
    lawvm bisect 2006/1299 --top 10
"""
from __future__ import annotations

import re
import sys
from typing import List, Literal, Tuple

import Levenshtein

from lawvm.finland.grafter import (
    get_corpus,
    _resolve_applicable_amendment_records,
    process_muutoslaki,
    get_ground_truth,
)
from lawvm.finland.statute import StatuteContext, ReplayState, _serialize_text_node
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.tools.editorial_hygiene import normalize_finlex_oracle_comparison_text


# ---------------------------------------------------------------------------
# Scoring — mirrors batch_test.py exactly
# ---------------------------------------------------------------------------

def _normalize(t: str) -> str:
    return normalize_finlex_oracle_comparison_text(t)


def _clean(t: str) -> str:
    return re.sub(r'[^a-z0-9äöå]', '', _normalize(t).lower())


def _score_master(state: "ReplayState", c_truth: str) -> float:
    """Score state.ir against the oracle via serialize_text."""
    text = _serialize_text_node(state.ir)
    return Levenshtein.ratio(_clean(text), c_truth)


# ---------------------------------------------------------------------------
# Bisect core
# ---------------------------------------------------------------------------

def bisect_statute(
    sid: str,
    mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    verbose: bool = False,
    top: int = 5,
) -> None:
    """Bisect a statute's amendment chain to find score-dropping amendments."""
    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: statute {sid} not found in zip", file=sys.stderr)
        sys.exit(1)

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    truth_text = get_ground_truth(sid)
    if not truth_text:
        print(f"ERROR: no oracle for {sid}", file=sys.stderr)
        sys.exit(1)
    c_truth = _clean(truth_text)

    amendment_records, cutoff_date, oracle_version_amendment_id = _resolve_applicable_amendment_records(
        sid, mode
    )
    n = len(amendment_records)

    print(f"Statute : {sid}")
    print(f"Mode    : {mode}")
    print(f"Oracle  : {len(truth_text):,} chars")
    print(f"Cutoff  : {cutoff_date.isoformat() if cutoff_date else '(none)'}")
    print(f"Oracle version : {oracle_version_amendment_id or '(none)'}")
    print(f"Amendments: {n}")
    print()

    # Score before any amendments
    baseline = _score_master(state, c_truth)

    # (amendment_id, score_after, score_before, delta, index)
    steps: List[Tuple[str, float, float, float, int]] = []
    prev_score = baseline

    for i, rec in enumerate(amendment_records, start=1):
        mid = str(rec["statute_id"])
        score_before = prev_score

        state = process_muutoslaki(mid, state, ctx, replay_mode=mode, parent_id=sid).output

        score_after = _score_master(state, c_truth)
        delta = score_after - score_before
        steps.append((mid, score_after, score_before, delta, i))
        prev_score = score_after

        if verbose:
            indicator = "▼" if delta < -0.005 else ("▲" if delta > 0.001 else " ")
            eff = rec.get("effective_date", "") or ""
            print(
                f"  [{i:3d}/{n}] {indicator} {mid:<12s}  "
                f"{score_before:.1%} → {score_after:.1%}  ({delta:+.1%})"
                + (f"  {eff}" if eff else "")
            )

    final_score = steps[-1][1] if steps else baseline

    # Find drops (delta < 0), sorted most-negative first
    drops = sorted(
        [(mid, score_after, score_before, delta, idx)
         for mid, score_after, score_before, delta, idx in steps
         if delta < 0],
        key=lambda x: x[3],
    )

    print()
    print(f"Baseline (no amendments) : {baseline:.1%}")
    print(f"Final score              : {final_score:.1%}")
    print(f"Total change             : {final_score - baseline:+.1%}")
    print()

    if not drops:
        print("No single-amendment score drops detected.")
        return

    print(f"Score drops (worst {min(top, len(drops))}):")
    for mid, score_after, score_before, delta, idx in drops[:top]:
        rec = amendment_records[idx - 1]
        eff = rec.get("effective_date", "") or ""
        title_snip = str(rec.get("title") or "")[:60]
        print(
            f"  [{idx:3d}/{n}] {mid:<12s}  "
            f"{score_before:.1%} → {score_after:.1%}  ({delta:+.1%})"
        )
        if title_snip:
            print(f"           {title_snip}")
        if eff:
            print(f"           Effective: {eff}")

    print()
    primary = drops[0]
    print(f"Primary suspect: {primary[0]}  (step {primary[4]}/{n}, "
          f"{primary[2]:.1%} → {primary[1]:.1%}, {primary[3]:+.1%})")
    print()
    print("To investigate:")
    print(f"  lawvm dump {sid} --after apply --source {primary[0]}")


def main(args) -> None:
    bisect_statute(
        sid=args.statute_id,
        mode=getattr(args, "mode", "finlex_oracle"),
        verbose=getattr(args, "verbose", False),
        top=getattr(args, "top", 5),
    )
