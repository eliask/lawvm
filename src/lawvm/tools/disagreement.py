"""lawvm disagreement — mine pipeline captures for high-leverage fix targets.

Two-phase tool:

  Phase 1 (--populate):
    For the top-N worst-scoring non-suspect statutes from a labeled bench run,
    run build_capture() and save JSON bundles to data/disagreement/<label>/.

  Phase 2 (--analyze):
    Scan all saved captures for a label and detect four disagreement patterns:
      EXTRACTION_MISS     — amendment has a rich body but PEG produced 0 ops
      ADDRESS_MISMATCH    — ops were compiled but failed to apply (target not found)
      SPARSE_PAYLOAD      — ops compiled but section snapshot is empty/tiny vs body
      PEG_UNDER_EXTRACT   — PEG claimed N target sections but body has significantly more

    Emits a ranked worklist (terminal + JSON) to data/disagreement/<label>/worklist.json.

Usage:
    lawvm disagreement --populate --top 50 --label disagree_v1
    lawvm disagreement --analyze  --label disagree_v1
    lawvm disagreement --populate --analyze --top 50 --label disagree_v1
"""
from __future__ import annotations

import csv
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _lawvm_dir() -> Path:
    """LawVM/ root — four levels up from this file."""
    return Path(__file__).resolve().parents[3]


def _disagreement_dir(label: str) -> Path:
    d = _lawvm_dir() / "data" / "disagreement" / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_corpus_path() -> Path:
    lawvm = _lawvm_dir()
    primary = lawvm / "data" / "finland" / "bench_corpus.csv"
    if primary.exists():
        return primary
    return lawvm / ".tmp" / "batch_test_list.csv"


# ---------------------------------------------------------------------------
# Load worst statutes from a bench run
# ---------------------------------------------------------------------------

def _load_bench_run(label: str) -> list[tuple[str, float]]:
    """Load per-statute results for a labeled bench run.

    Returns [(sid, similarity)] sorted worst-first (lowest similarity first).
    """
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        print(f"ERROR: no bench run found for label '{label}'", file=sys.stderr)
        print(f"  Looked in: {runs_dir}", file=sys.stderr)
        sys.exit(1)
    path = candidates[-1]
    results: list[tuple[str, float]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["statute_id"]
            sim_str = row.get("similarity", "ERR")
            try:
                sim = float(sim_str)
            except ValueError:
                sim = -1.0
            results.append((sid, sim))
    # Sort worst-first; skip errors (sim < 0)
    results = [(sid, sim) for sid, sim in results if 0.0 <= sim < 1.0]
    results.sort(key=lambda x: x[1])
    return results


# ---------------------------------------------------------------------------
# Populate: run captures for top-N worst statutes
# ---------------------------------------------------------------------------

def _sid_to_filename(sid: str) -> str:
    return sid.replace("/", "_") + ".json"


def _is_suspect(sid: str) -> bool:
    """Very cheap suspect check: skip if bench run score is errored or oracle absent.

    We skip this for now — the caller already excludes sim<0 statutes.
    """
    return False


def populate(label: str, top: int, force: bool = False) -> None:
    """Populate data/disagreement/<label>/ with capture JSONs for top-N worst statutes."""
    from lawvm.tools.capture import build_capture

    out_dir = _disagreement_dir(label)
    worst = _load_bench_run(label)[:top]

    if not worst:
        print(f"No eligible statutes found in bench run '{label}'.")
        return

    print(f"Populating {len(worst)} captures for label='{label}' → {out_dir}")
    ok = 0
    skipped = 0
    errors = 0

    for i, (sid, sim) in enumerate(worst, 1):
        fname = _sid_to_filename(sid)
        out_path = out_dir / fname
        if out_path.exists() and not force:
            skipped += 1
            print(f"  [{i}/{len(worst)}] SKIP (cached) {sid}  err={100*(1-sim):.2f}%")
            continue

        print(f"  [{i}/{len(worst)}] CAPTURE {sid}  err={100*(1-sim):.2f}%", end="", flush=True)
        try:
            with redirect_stdout(io.StringIO()):
                bundle = build_capture(sid)
            out_path.write_text(
                json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            ok += 1
            print(" OK")
        except Exception as e:
            errors += 1
            print(f" ERROR: {e}")

    print(f"\nDone: {ok} captured, {skipped} cached, {errors} errors.")


# ---------------------------------------------------------------------------
# Analysis: mine captures for disagreement patterns
# ---------------------------------------------------------------------------

# Heuristic thresholds
_BODY_SECTION_THRESHOLD = 3       # body must have > this many sections to flag EXTRACTION_MISS
_BODY_MIN_FOR_PEG_UNDER = 4       # body must have > this many sections for PEG_UNDER_EXTRACT
_PEG_UNDER_RATIO = 2.0            # body_sections / peg_claimed_sections > this → PEG_UNDER_EXTRACT
_SPARSE_PAYLOAD_RATIO = 0.15      # payload_section_count / body_section_count < this → SPARSE_PAYLOAD


def _count_peg_target_sections(canonical_ops: list[dict[str, Any]]) -> int:
    """Count unique target section numbers referenced by canonical ops."""
    sections: set[str] = set()
    for op in canonical_ops:
        target = op.get("target", "")
        # target is a string like "section:5" or "chapter:2/section:3/subsection:1"
        # extract the section component
        for part in str(target).split("/"):
            if part.startswith("section:"):
                sections.add(part)
                break
    return len(sections)


def _count_payload_section_count(canonical_ops: list[dict[str, Any]]) -> int:
    """Count total sections covered by canonical op payloads (rough: count ops with payloads)."""
    return sum(1 for op in canonical_ops if op.get("payload") is not None)


def _analyze_amendment(
    statute_id: str,
    amend: dict[str, Any],
) -> list[dict[str, Any]]:
    """Analyze one amendment bundle for disagreement patterns.

    Returns a list of finding dicts (one per pattern detected).
    """
    findings: list[dict[str, Any]] = []

    amendment_id = amend.get("statute_id", "?")
    body_shape = amend.get("body_shape") or {}
    body_section_count = body_shape.get("section_count", 0)

    counts = amend.get("counts", {})
    compiled_ops_count = counts.get("compiled_ops", 0)
    canonical_ops_count = counts.get("canonical_ops", 0)
    failed_ops_count = counts.get("failed_ops", 0)

    canonical_ops: list[dict[str, Any]] = amend.get("canonical_ops", [])
    failed_ops: list[dict[str, Any]] = amend.get("failed_ops", [])

    # --- EXTRACTION_MISS ---
    # Amendment has rich body (many sections) but PEG produced 0 compiled ops.
    # This means the johtolause wasn't parsed at all → all body content ignored.
    if (
        canonical_ops_count == 0
        and compiled_ops_count == 0
        and body_section_count > _BODY_SECTION_THRESHOLD
        and amend.get("source_available", False)
    ):
        findings.append({
            "pattern": "EXTRACTION_MISS",
            "statute_id": statute_id,
            "amendment_id": amendment_id,
            "body_sections": body_section_count,
            "peg_ops": 0,
            "est_affected_sections": body_section_count,
            "detail": "PEG produced 0 ops; body has sections → johtolause parse failure",
        })

    # --- ADDRESS_MISMATCH ---
    # PEG produced ops and they compiled, but then failed to apply.
    # Each failed_op = one provision the pipeline tried but couldn't resolve.
    if failed_ops_count > 0 and (canonical_ops_count + compiled_ops_count) > 0:
        # Collect distinct failure reasons
        reason_counts: dict[str, int] = {}
        for f in failed_ops:
            reason = f.get("reason", "unknown") if isinstance(f, dict) else "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        findings.append({
            "pattern": "ADDRESS_MISMATCH",
            "statute_id": statute_id,
            "amendment_id": amendment_id,
            "failed_count": failed_ops_count,
            "canonical_count": canonical_ops_count,
            "est_affected_sections": failed_ops_count,
            "reason_breakdown": reason_counts,
            "detail": f"{failed_ops_count} ops compiled but failed to apply",
        })

    # --- PEG_UNDER_EXTRACT ---
    # PEG claimed it's modifying N sections (from canonical ops targets),
    # but the amendment body has significantly more sections.
    # Suggests PEG parsed only part of the johtolause.
    if (
        body_section_count > _BODY_MIN_FOR_PEG_UNDER
        and canonical_ops_count > 0
    ):
        peg_claimed = _count_peg_target_sections(canonical_ops)
        if peg_claimed > 0 and body_section_count / peg_claimed > _PEG_UNDER_RATIO:
            findings.append({
                "pattern": "PEG_UNDER_EXTRACT",
                "statute_id": statute_id,
                "amendment_id": amendment_id,
                "body_sections": body_section_count,
                "peg_claimed_sections": peg_claimed,
                "ratio": round(body_section_count / peg_claimed, 1),
                "est_affected_sections": body_section_count - peg_claimed,
                "detail": (
                    f"PEG claimed {peg_claimed} target sections; "
                    f"body has {body_section_count} → ratio {body_section_count/peg_claimed:.1f}x"
                ),
            })

    # --- SPARSE_PAYLOAD ---
    # Ops compiled and canonicalized, but only a small fraction of the body
    # sections are covered by payloads. Suggests materialization/payload
    # extraction failure (ops say "replace" but the new text wasn't attached).
    if (
        body_section_count > _BODY_SECTION_THRESHOLD
        and canonical_ops_count > 0
    ):
        payload_covered = _count_payload_section_count(canonical_ops)
        if body_section_count > 0:
            ratio = payload_covered / body_section_count
            if ratio < _SPARSE_PAYLOAD_RATIO and payload_covered < canonical_ops_count:
                findings.append({
                    "pattern": "SPARSE_PAYLOAD",
                    "statute_id": statute_id,
                    "amendment_id": amendment_id,
                    "body_sections": body_section_count,
                    "payload_covered": payload_covered,
                    "canonical_ops": canonical_ops_count,
                    "coverage_ratio": round(ratio, 3),
                    "est_affected_sections": body_section_count - payload_covered,
                    "detail": (
                        f"{payload_covered}/{body_section_count} body sections "
                        f"have payload ({ratio:.1%}) — likely materialization gap"
                    ),
                })

    return findings


def _analyze_capture(path: Path) -> list[dict[str, Any]]:
    """Load one capture JSON and analyze all its amendments."""
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARNING: could not read {path.name}: {e}", file=sys.stderr)
        return []

    statute_id = bundle.get("statute_id", path.stem.replace("_", "/"))
    findings: list[dict[str, Any]] = []
    for amend in bundle.get("amendments", []):
        if not amend.get("included", True):
            continue  # excluded from this replay
        findings.extend(_analyze_amendment(statute_id, amend))
    return findings


# ---------------------------------------------------------------------------
# Worklist output
# ---------------------------------------------------------------------------

_PATTERN_ORDER = ["EXTRACTION_MISS", "PEG_UNDER_EXTRACT", "ADDRESS_MISMATCH", "SPARSE_PAYLOAD"]


def _print_worklist(all_findings: list[dict[str, Any]], label: str) -> None:
    """Print ranked worklist to stdout."""
    if not all_findings:
        print(f"\n=== Disagreement Worklist (label={label}) ===")
        print("  No disagreements found.")
        return

    # Aggregate by pattern
    by_pattern: dict[str, list[dict[str, Any]]] = {}
    for f in all_findings:
        p = f["pattern"]
        by_pattern.setdefault(p, []).append(f)

    # Sort each pattern's findings by est_affected_sections descending
    for p in by_pattern:
        by_pattern[p].sort(key=lambda x: x.get("est_affected_sections", 0), reverse=True)

    print(f"\n=== Disagreement Worklist  label={label} ===\n")
    print(f"{'Type':<25}  {'Count':>5}  {'Est. sections affected':>22}")
    print("-" * 58)
    for p in _PATTERN_ORDER:
        findings = by_pattern.get(p, [])
        total_secs = sum(f.get("est_affected_sections", 0) for f in findings)
        if findings:
            print(f"  {p:<23}  {len(findings):>5}  {total_secs:>22}")

    # Top 10 overall by est_affected_sections
    top = sorted(all_findings, key=lambda x: x.get("est_affected_sections", 0), reverse=True)[:10]
    print(f"\nTop {min(10, len(top))} highest-leverage amendments:")
    for i, f in enumerate(top, 1):
        sid = f["statute_id"]
        amendment_id = f["amendment_id"]
        secs = f.get("est_affected_sections", 0)
        pat = f["pattern"]
        detail = f.get("detail", "")
        print(f"  {i:2d}. {amendment_id} → {sid}: {secs} uncovered sections ({pat})")
        if detail:
            print(f"       {detail}")

    print()


def analyze(label: str, verbose: bool = False) -> None:
    """Analyze all captures for a label and emit a worklist."""
    capture_dir = _disagreement_dir(label)
    capture_files = sorted(capture_dir.glob("*.json"))
    # exclude worklist.json itself
    capture_files = [f for f in capture_files if f.name != "worklist.json"]

    if not capture_files:
        print(f"No captures found in {capture_dir}.")
        print("Run: lawvm disagreement --populate --label <label>")
        return

    print(f"Analyzing {len(capture_files)} captures for label='{label}'...")
    all_findings: list[dict[str, Any]] = []

    for path in capture_files:
        findings = _analyze_capture(path)
        if verbose and findings:
            for f in findings:
                print(f"  {f['pattern']:25s} {f.get('amendment_id','?'):15s} → {f['statute_id']}  "
                      f"~{f.get('est_affected_sections',0)} secs")
        all_findings.extend(findings)

    _print_worklist(all_findings, label)

    # Save worklist JSON
    worklist_path = capture_dir / "worklist.json"
    worklist_path.write_text(
        json.dumps(
            {
                "label": label,
                "capture_count": len(capture_files),
                "findings": all_findings,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Worklist saved to {worklist_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    label: str = args.label
    if not label:
        print("ERROR: --label is required", file=sys.stderr)
        sys.exit(1)

    did_something = False

    if getattr(args, "populate", False):
        did_something = True
        top: int = getattr(args, "top", 50)
        force: bool = getattr(args, "force", False)
        populate(label, top=top, force=force)

    if getattr(args, "analyze", False):
        did_something = True
        verbose: bool = getattr(args, "verbose", False)
        analyze(label, verbose=verbose)

    if not did_something:
        print("ERROR: specify --populate and/or --analyze", file=sys.stderr)
        sys.exit(1)
