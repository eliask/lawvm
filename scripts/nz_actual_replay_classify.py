"""NZ replay-actual classification sweep (in-process).

For each work in the smoke corpus, run actual replay over all four promotable
families and classify the outcome by transition count + refusal rule counts.
Pure measurement: never mutates the archive.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from lawvm.new_zealand.actual_replay import NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES, build_archived_work_actual_replay


def main() -> None:
    smoke = Path("data/nz/bench_corpus_smoke.csv")
    db = Path("data/nz_legislation.farchive")
    rows = list(csv.DictReader(smoke.open()))
    out = Path("/tmp/nz_actual_replay_sweep.jsonl")
    out.unlink(missing_ok=True)

    n_replayed = 0
    n_refused = 0
    n_neither = 0
    rule_totals: dict[str, int] = {}

    with out.open("w") as f:
        for row in rows:
            wid = row["work_id"]
            try:
                report = build_archived_work_actual_replay(db, work_id=wid, families=NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES)
            except Exception as exc:  # bounded diagnostic sweep only
                f.write(json.dumps({"work_id": wid, "error": repr(exc)[:300]}) + "\n")
                continue
            summary = report.summary()
            replayed = summary["transitions_replayed"]
            refused = summary["transitions_refused"]
            if replayed:
                n_replayed += 1
            if refused:
                n_refused += 1
            if not replayed and not refused:
                n_neither += 1
            for rule, count in summary["refusal_rule_counts"].items():
                rule_totals[rule] = rule_totals.get(rule, 0) + count
            f.write(json.dumps({
                "work_id": wid,
                "replayed": replayed,
                "refused": refused,
                "ops_replayed": summary["ops_replayed"],
                "target_slice_agreements": summary["target_slice_agreements"],
                "target_slice_nodes": summary["target_slice_nodes"],
                "all_slices_agree": summary["all_slices_agree"],
                "refusal_rule_counts": summary["refusal_rule_counts"],
                "residual_family_counts": summary["residual_family_counts"],
                "residual_status_counts": summary["residual_status_counts"],
                "families_not_attempted": summary["families_not_attempted"],
                "family_level_dry_run_refusal_counts": summary.get(
                    "family_level_dry_run_refusal_counts", {}
                ),
            }, ensure_ascii=False) + "\n")

    print(f"sweep: {len(rows)} works  replayed={n_replayed}  refused={n_refused}  neither={n_neither}")
    print("rule totals across smoke:")
    for rule, count in sorted(rule_totals.items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {rule}")


if __name__ == "__main__":
    main()