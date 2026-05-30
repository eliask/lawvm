#!/usr/bin/env python3
"""Farchive-native broad UK replay-vs-oracle baseline.

The 9-statute gate (``scripts/uk_regression_test.py``) is too narrow to detect
regressions in oracle grounding, which touches *every* statute's score. This
tool scores replay-vs-oracle EID-set similarity for an arbitrary sample of UK
statutes drawn straight from the farchive (no on-disk raw XML required), so a
grounding change can be checked against a broad baseline before it ships.

Two scoring lanes per statute:
  - ``aligned``   : apply_ops with oracle EID alignment (the production score).
  - ``unaligned`` : apply_ops with ``allow_oracle_alignment=False`` (structural
                    replay only). The aligned/unaligned gap is the #53 signal —
                    when grounding is unstable the aligned score moves under node
                    removal while the unaligned score does not.

Each statute is scored in its OWN subprocess (``--one ID``) so peak RSS stays
bounded under WSL2 (per the source-root-lifecycle note); the driver forks one
child per statute and aggregates a JSON snapshot.

Usage:
  # score an explicit list, write a snapshot
  uv run python scripts/uk_broad_baseline.py --ids ukpga/1978/30 ukpga/1985/6 \
      --out .tmp/uk_baseline.json

  # sample N statutes that have BOTH enacted+current in the archive
  uv run python scripts/uk_broad_baseline.py --sample 150 --seed 7 \
      --out .tmp/uk_baseline.json

  # score one statute (subprocess unit; prints one JSON line)
  uv run python scripts/uk_broad_baseline.py --one ukpga/1978/30

  # compare two snapshots (regression gate)
  uv run python scripts/uk_broad_baseline.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"

# A statute is flagged a regression if its aligned score drops by more than this
# many percentage points versus the baseline snapshot.
_REGRESSION_TOL = 0.1


def _eids(nodes: list[Any], pit_date: Optional[str] = None) -> set[str]:
    from lawvm.core.ir_helpers import is_zombie

    out: set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            out.add(eid)
        out.update(_eids(n.children, pit_date=pit_date))
    return out


def _similarity(replay_eids: set[str], oracle_eids: set[str]) -> float:
    if not replay_eids and not oracle_eids:
        return 1.0
    common = replay_eids & oracle_eids
    return len(common) / max(len(replay_eids), len(oracle_eids), 1)


def score_one(statute_id: str) -> dict[str, Any]:
    """Score one statute from the farchive. Returns a result dict (never raises)."""
    from farchive import Farchive
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline
    from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes, parse_uk_statute_ir_bytes

    result: dict[str, Any] = {"statute_id": statute_id}
    archive = Farchive(DB_PATH)
    try:
        enacted = archive.get(f"{_LEG_BASE}/{statute_id}/enacted/data.xml")
        current = archive.get(f"{_LEG_BASE}/{statute_id}/data.xml")
        if not enacted:
            return {**result, "error": "enacted_missing"}
        if not current:
            return {**result, "error": "current_missing"}

        oracle_ir = parse_uk_statute_ir_bytes(current, statute_id=statute_id)
        oracle_eids = _eids([oracle_ir.body]) | {
            e for s in oracle_ir.supplements for e in _eids([s])
        }

        oracle_data = extract_eid_map_bytes(current)
        eid_map = oracle_data.get("eid_map", {})
        text_map = oracle_data.get("text_map", {})

        pipeline = UKReplayPipeline(REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(statute_id, archive=archive)
        result["n_ops"] = len(ops)

        lanes: dict[str, float] = {}
        for lane, aligned in (("aligned", True), ("unaligned", False)):
            base_ir = parse_uk_statute_ir_bytes(enacted, statute_id=statute_id)
            replayed = pipeline.apply_ops(
                base_ir, ops, eid_map=eid_map, text_map=text_map, allow_oracle_alignment=aligned
            )
            replay_eids = _eids([replayed.body]) | {
                e for s in replayed.supplements for e in _eids([s])
            }
            lanes[lane] = round(100.0 * _similarity(replay_eids, oracle_eids), 2)
            if lane == "aligned":
                result["n_replay"] = len(replay_eids)
        result["n_oracle"] = len(oracle_eids)
        result["aligned"] = lanes["aligned"]
        result["unaligned"] = lanes["unaligned"]
        return result
    except Exception as exc:  # noqa: BLE001 — a broken statute must not abort the sweep
        return {**result, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        archive.close()


def sample_statutes(n: int, seed: int, classes: Optional[list[str]]) -> list[str]:
    """Sample n statute IDs that have BOTH enacted and current XML in the archive."""
    from farchive import Farchive

    archive = Farchive(DB_PATH)
    try:
        enacted = set()
        current = set()
        suffix_enacted = "/enacted/data.xml"
        suffix_current = "/data.xml"
        for loc in archive.locators(f"{_LEG_BASE}/%/enacted/data.xml"):
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_enacted)]
            enacted.add(sid)
        for loc in archive.locators(f"{_LEG_BASE}/%/data.xml"):
            if loc.endswith(suffix_enacted):
                continue
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_current)]
            # only act-level ids (act_type/year/number), not affecting/changes URLs
            if sid.count("/") == 2 and "/changes/" not in loc and "/affecting/" not in loc:
                current.add(sid)
    finally:
        archive.close()

    both = sorted(enacted & current)
    if classes:
        both = [s for s in both if s.split("/", 1)[0] in classes]
    rng = random.Random(seed)
    rng.shuffle(both)
    return both[:n]


def run_driver(ids: list[str], out: Optional[Path]) -> int:
    results: list[dict[str, Any]] = []
    for i, sid in enumerate(ids, 1):
        proc = subprocess.run(
            [sys.executable, __file__, "--one", sid],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        row: dict[str, Any]
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, IndexError):
            row = {"statute_id": sid, "error": f"subprocess_exit_{proc.returncode}"}
            if proc.stderr.strip():
                row["stderr_tail"] = proc.stderr.strip().splitlines()[-1][:200]
        results.append(row)
        if "error" in row:
            print(f"[{i}/{len(ids)}] {sid:24s} ERROR {row['error']}", flush=True)
        else:
            print(
                f"[{i}/{len(ids)}] {sid:24s} aligned={row['aligned']:5.1f}% "
                f"unaligned={row['unaligned']:5.1f}% "
                f"(replay={row.get('n_replay')} oracle={row.get('n_oracle')})",
                flush=True,
            )

    snapshot = {r["statute_id"]: r for r in results}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
        print(f"\nWrote {len(snapshot)} rows -> {out}")

    scored = [r for r in results if "error" not in r]
    errored = [r for r in results if "error" in r]
    if scored:
        avg = sum(r["aligned"] for r in scored) / len(scored)
        print(f"\nScored {len(scored)} / {len(results)}  mean aligned={avg:.2f}%  errors={len(errored)}")
    return 0


def run_compare(before_path: Path, after_path: Path) -> int:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    regressions: list[tuple[str, float, float]] = []
    improvements: list[tuple[str, float, float]] = []
    for sid, a in after.items():
        b = before.get(sid)
        if not b or "aligned" not in a or "aligned" not in b:
            continue
        delta = a["aligned"] - b["aligned"]
        if delta < -_REGRESSION_TOL:
            regressions.append((sid, b["aligned"], a["aligned"]))
        elif delta > _REGRESSION_TOL:
            improvements.append((sid, b["aligned"], a["aligned"]))

    for sid, b, a in sorted(improvements, key=lambda x: x[2] - x[1], reverse=True):
        print(f"  IMPROVED   {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")
    for sid, b, a in sorted(regressions, key=lambda x: x[2] - x[1]):
        print(f"  REGRESSION {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")

    print(f"\n{len(improvements)} improved, {len(regressions)} regressed")
    return 1 if regressions else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--one", metavar="ID", help="Score a single statute (subprocess unit; prints one JSON line)")
    ap.add_argument("--ids", nargs="+", help="Explicit statute IDs to score")
    ap.add_argument("--sample", type=int, help="Sample N statutes with both enacted+current in the archive")
    ap.add_argument("--seed", type=int, default=0, help="Sample RNG seed (default 0)")
    ap.add_argument("--classes", nargs="+", help="Restrict sample to these act-type classes (e.g. ukpga uksi)")
    ap.add_argument("--out", type=Path, help="Write JSON snapshot here")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two snapshots")
    args = ap.parse_args()

    if args.one:
        print(json.dumps(score_one(args.one)))
        return 0
    if args.compare:
        return run_compare(Path(args.compare[0]), Path(args.compare[1]))

    ids: list[str] = []
    if args.ids:
        ids.extend(args.ids)
    if args.sample:
        ids.extend(sample_statutes(args.sample, args.seed, args.classes))
    if not ids:
        ap.error("nothing to do: pass --one, --ids, --sample, or --compare")
    return run_driver(ids, args.out)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
