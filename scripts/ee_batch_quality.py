"""EE batch quality runner — consecutive terviktekst pair replays.

For each consecutive pair (base→next) in a statute's redactions chain, runs
replay_ee_to_pit and records divergence stats. Useful for systematic quality
measurement across a full amendment chain.

Columns:
  ops  = number of amendment ops applied
  mm   = MISMATCH (text differs between replay and oracle)
  om   = OPS_MISSING (section in oracle not in replay)
  cm   = CONSOLIDATED_MISSING (section in replay not in oracle)
  tot  = total divergences (mm + om + cm)

Usage (from LawVM/ dir):
    uv run python scripts/ee_batch_quality.py
    uv run python scripts/ee_batch_quality.py 162951           # Courts Act only
    uv run python scripts/ee_batch_quality.py 162951 123456    # multiple
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lawvm.estonia.fetch import fetch_redactions_feed, open_rt_archive
from lawvm.estonia.replay import replay_ee_to_pit

# Known statute grupiIds (add more as discovered)
KNOWN_STATUTES: dict[str, str] = {
    "162951": "Kohtute seadus (Courts Act)",
}


def run_chain(grupi_id: str, label: str, archive) -> dict:
    redactions = fetch_redactions_feed(grupi_id, archive)
    redactions = sorted(redactions, key=lambda r: r.effective)

    print(f"\n{'=' * 72}")
    print(f"  {label}  (grupiId={grupi_id})")
    print(f"  {len(redactions)} redactions → {max(0, len(redactions)-1)} consecutive pairs")
    print(f"{'=' * 72}")

    if len(redactions) < 2:
        print("  (not enough redactions to replay, need at least 2)")
        return {}

    hdr = (
        f"  {'base_id':<15} {'as_of':<12} {'oracle_id':<15}"
        f" {'ops':>4} {'mm':>4} {'om':>4} {'cm':>4} {'tot':>4}  notes"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    totals: dict = dict(pairs=0, ops=0, mm=0, om=0, cm=0, tot=0, errors=0, perfect=0)

    for base_r, next_r in zip(redactions, redactions[1:], strict=False):
        result = replay_ee_to_pit(
            base_r.aktViide, next_r.effective, archive, verbose=False
        )

        if result.error:
            errmsg = result.error[:55]
            print(
                f"  {base_r.aktViide:<15} {next_r.effective:<12} {'ERROR':<15}"
                f"                   {errmsg}"
            )
            totals["errors"] += 1
            continue

        total_divs = len(result.divergences)
        oracle_id = result.oracle_id or "(none)"

        notes = ""
        if result.oracle is None:
            notes = "NO_ORACLE"
        elif total_divs == 0:
            notes = "PERFECT"
            totals["perfect"] += 1

        print(
            f"  {base_r.aktViide:<15} {next_r.effective:<12} {oracle_id:<15}"
            f" {result.n_ops:>4} {result.n_mismatch:>4}"
            f" {result.n_ops_missing:>4} {result.n_con_missing:>4}"
            f" {total_divs:>4}  {notes}"
        )

        totals["pairs"] += 1
        totals["ops"] += result.n_ops
        totals["mm"] += result.n_mismatch
        totals["om"] += result.n_ops_missing
        totals["cm"] += result.n_con_missing
        totals["tot"] += total_divs

    print("  " + "-" * (len(hdr) - 2))
    print(
        f"  {'TOTAL':<15} {'':<12} {'':<15}"
        f" {totals['ops']:>4} {totals['mm']:>4}"
        f" {totals['om']:>4} {totals['cm']:>4}"
        f" {totals['tot']:>4}"
        f"  ({totals['pairs']} pairs, {totals['perfect']} perfect,"
        f" {totals['errors']} errors)"
    )
    if totals["pairs"] > 0:
        avg = totals["tot"] / totals["pairs"]
        perfect_pct = 100 * totals["perfect"] / totals["pairs"]
        print(f"  avg divs/pair: {avg:.1f}   perfect pairs: {perfect_pct:.0f}%")

    return totals


def main() -> None:
    grupi_ids = sys.argv[1:] if len(sys.argv) > 1 else list(KNOWN_STATUTES.keys())

    archive = open_rt_archive()

    grand: dict = dict(pairs=0, ops=0, mm=0, om=0, cm=0, tot=0, errors=0, perfect=0)
    for gid in grupi_ids:
        label = KNOWN_STATUTES.get(gid, f"grupiId={gid}")
        chain_totals = run_chain(gid, label, archive)
        for k in grand:
            grand[k] += chain_totals.get(k, 0)

    if len(grupi_ids) > 1 and grand["pairs"] > 0:
        print(f"\n{'=' * 72}")
        print(f"  GRAND TOTAL: {grand['pairs']} pairs across {len(grupi_ids)} statutes")
        print(
            f"  ops={grand['ops']}  mm={grand['mm']}  om={grand['om']}"
            f"  cm={grand['cm']}  tot={grand['tot']}"
            f"  perfect={grand['perfect']}  errors={grand['errors']}"
        )
        print(
            f"  avg divs/pair: {grand['tot']/grand['pairs']:.1f}"
            f"   perfect: {100*grand['perfect']/grand['pairs']:.0f}%"
        )

    print()


if __name__ == "__main__":
    main()
