import subprocess
import re
import sys

CORPUS = [
    "ukpga/1998/42",
    "ukpga/1978/30",
    "ukpga/2025/18",
    "eur/2016/679",
    "nia/2000/1",
    "asp/2000/6",
    "asc/2020/1"
]

BASELINES = {
    # Baselines post parenthesized-range-expansion fix (2026-03-24).
    # HRA 1998: 90.3→96.1 via _split_metadata_provisions fix for s.N(MA)-(MD) ranges
    "ukpga/1998/42": 96.1,
    "ukpga/1978/30": 72.9,
    "ukpga/2025/18": 88.3,
    "eur/2016/679": 80.6,
    "nia/2000/1": 93.3,
    "asp/2000/6": 71.3,
    "asc/2020/1": 99.6
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
