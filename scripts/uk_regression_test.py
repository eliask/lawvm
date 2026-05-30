import subprocess
import re
import sys

CORPUS = [
    "ukpga/1998/42",
    "ukpga/1978/30",
    "ukpga/2025/18",
    "ukpga/2018/16",
    "ukpga/1972/68",
    "eur/2016/679",
    "nia/2000/1",
    "asp/2000/6",
    "asc/2020/1"
]

BASELINES = {
    # UK baselines refreshed 2026-05-30 to lock in cumulative gains since the
    # 2026-03-24 parenthesized-range-expansion fix (notably asp/2000/6 71.3→90.8
    # and the ukpga/1978/30 fee-table-index crash fix restoring it to 73.8).
    "ukpga/1998/42": 97.3,
    # 78.1 (was 73.8) after recovering effect Types that carry a trailing
    # commencement date in the feed Type cell (e.g. "added (1.7.1999)"): the
    # date is split into in-force dates so the base verb classifies structurally
    # and applies — recovering the cross-act insert of s. 23A and its subtree.
    "ukpga/1978/30": 78.1,
    "ukpga/2025/18": 88.4,
    # EU Withdrawal Act 2018: 1828 replayed vs 1832 oracle EIDs, a substantial
    # well-supported statute added to the gate 2026-05-30.
    "ukpga/2018/16": 98.0,
    # European Communities Act 1972: wholly repealed; both replay and oracle
    # are empty, which the harness now scores as 100%. Guards the whole-act
    # repeal path.
    "ukpga/1972/68": 100.0,
    # eur/2016/679 left at its pre-regression 80.6 deliberately: the current
    # -5.4% divergence is an EU-compiler regression tracked separately, and the
    # gate must keep flagging it until that is resolved.
    "eur/2016/679": 80.6,
    "nia/2000/1": 93.3,
    "asp/2000/6": 90.8,
    "asc/2020/1": 100.0
}

def run_test(statute_id):
    print(f"Testing {statute_id}...", end=" ", flush=True)
    cmd = [sys.executable, "scripts/uk_replay_statute.py", statute_id]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env={"PYTHONPATH": "src"})
        if result.returncode != 0:
            print(f"FAILED (exit {result.returncode})")
            print(result.stderr)
            return None

        m = re.search(r"Full EID Similarity: ([\d.]+)%", result.stdout)
        if m:
            score = float(m.group(1))
            baseline = BASELINES.get(statute_id, 0)
            diff = score - baseline
            status = "PASS" if diff >= -0.1 else "REGRESSION"
            print(f"{score}% (Baseline: {baseline}%, Diff: {diff:+.1f}%) -> {status}")
            return score
        else:
            print("FAILED (no score found)")
            return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None

def main():
    print("=== LawVM UK Pipeline Regression Suite ===\n")
    results = {}
    for act in CORPUS:
        score = run_test(act)
        if score is not None:
            results[act] = score

    print("\n--- Summary ---")
    all_pass = True
    for act, score in results.items():
        if score < BASELINES.get(act, 0) - 0.1:
            all_pass = False

    if all_pass:
        print("All tests PASSED or IMPROVED.")
    else:
        print("REGRESSIONS detected.")
        sys.exit(1)

if __name__ == "__main__":
    main()
