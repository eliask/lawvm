"""lawvm gold — gold master dataset management.

The Finlex oracle is an editorial materialization with known staleness and
convention issues. The gold master is a verified dataset where each statute's
correctness is established by independent logical trace.

Manifest v4 format (per-statute JSON files):
  manifest.json:
    statutes: list of {statute_id, title, oracle_version, n_provisions,
                       tier1, tier2, tier3, file}
  data/gold/<file>:
    provisions: dict of address → {text, tier, source, note, verified}
    Tier 1 = known anomaly (oracle stale or confirmed replay bug)
    Tier 2 = correct (replay matches oracle + LLM-verified)

Usage:
    lawvm gold status                     # summary of gold master state
    lawvm gold verify <statute_id>        # re-verify against stored gold texts
    lawvm gold verify --strict            # check strictness for all gold statutes
    lawvm gold verify <statute_id> --strict  # check strictness for one statute
    lawvm gold export                     # dump manifest as JSON
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import Levenshtein


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _gold_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent / "data" / "gold"


def _manifest_path() -> Path:
    return _gold_dir() / "manifest.json"


def _load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"version": 1, "statutes": {}}


def _save_manifest(manifest: dict) -> None:
    p = _manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Classification helpers (re-used from oracle_check, kept independent)
# ---------------------------------------------------------------------------

ORACLE_CATEGORIES = {"ORACLE_STALE", "EDITORIAL_CONVENTION"}
REPLAY_BUG_CATEGORIES = {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN"}


def _auto_tier(score: float, section_results: list) -> int:
    """Derive tier from oracle-check classification results."""
    if not section_results and score >= 0.9999:
        # Truly perfect: replay == oracle at section level
        return 2
    if not section_results:
        # Divergence exists but not captured at section level (preamble, intro)
        return 4
    diags = {d for _, d in section_results}
    if not diags - ORACLE_CATEGORIES:
        # All captured divergences are oracle issues
        return 3
    # Has unresolved replay bugs
    return 4


def _classify_statute_for_gold(sid: str, mode: Literal["finlex_oracle", "legal_pit"]):
    """Run oracle-check classification and return (score, section_results, title)."""
    from lawvm.tools.oracle_check import _classify_statute
    result = _classify_statute(sid, mode)
    return result


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _get_statutes_list(manifest: dict) -> List[dict]:
    """Return statutes as a list regardless of manifest version."""
    statutes = manifest.get("statutes", [])
    if isinstance(statutes, list):
        return statutes
    # v1 format: dict keyed by statute_id
    return [{"statute_id": sid, **entry} for sid, entry in statutes.items()]


def _cmd_status(manifest: dict, verbose: bool) -> None:
    statutes = _get_statutes_list(manifest)
    if not statutes:
        print("Gold master is empty.")
        return

    total_provisions = sum(s.get("n_provisions", 0) for s in statutes)
    tier1_provs = sum(s.get("tier1", 0) for s in statutes)
    tier2_provs = sum(s.get("tier2", 0) for s in statutes)
    print(f"Gold master v{manifest.get('version', '?')}: {len(statutes)} statutes, "
          f"{total_provisions} provisions")
    print(f"  Tier-2 correct: {tier2_provs}   Tier-1 known anomalies: {tier1_provs}\n")

    global_date = manifest.get("created", "?")[:10]
    for s in statutes:
        sid = s.get("statute_id", "?")
        title = s.get("title", "")[:55]
        n = s.get("n_provisions", 0)
        t1 = s.get("tier1", 0)
        t2 = s.get("tier2", 0)
        vdate = s.get("verified_date", s.get("verified_at", global_date))[:10]
        anomaly_info = f"  [{t1} anomaly]" if t1 else ""
        print(f"  {sid}  {t2}/{n} correct{anomaly_info}  [{vdate}]  {title}")


def _cmd_promote(sid: str, mode: Literal["finlex_oracle", "legal_pit"], forced_tier: Optional[int]) -> None:
    manifest = _load_manifest()
    statutes = manifest.setdefault("statutes", {})

    print(f"Classifying {sid}...")
    result = _classify_statute_for_gold(sid, mode)

    if result is None or result.error:
        err = result.error if result else "no result"
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    score = result.overall_score
    section_results = result.section_results
    title = result.title

    auto_tier = _auto_tier(score, section_results)
    tier = forced_tier if forced_tier is not None else auto_tier

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    verifier = "lawvm-auto" if forced_tier is None else "human"

    entry = {
        "tier": tier,
        "score": round(score, 6),
        "verified_at": now,
        "verifier": verifier,
        "title": title,
        "notes": "",
        "divergences": [
            {"section": k, "diagnosis": d}
            for k, d in sorted(section_results)
        ],
    }

    old_tier = statutes.get(sid, {}).get("tier")
    statutes[sid] = entry
    _save_manifest(manifest)

    tier_change = f"(was tier {old_tier})" if old_tier and old_tier != tier else "(new)"
    print(f"  {sid}: tier {tier} {tier_change}  score={score:.1%}")
    if section_results:
        from collections import Counter
        counts = Counter(d for _, d in section_results)
        parts = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  Divergences: {parts}")
    if tier == 1 and forced_tier != 1:
        print("  NOTE: tier 1 requires human sign-off. Use --tier 1 to confirm.")
    print("  Saved to manifest.")


def _norm_text(t: str) -> str:
    """Normalize text for comparison (strip whitespace variants, lowercase)."""
    t = re.sub(r'[\xa0\u2009\u202f]', ' ', t)  # NBSP → space
    return re.sub(r'\s+', ' ', t.strip().lower())


def _clean_text(t: str) -> str:
    """Remove non-alphanumeric for fuzzy comparison."""
    return re.sub(r'[^a-z0-9äöå]', '', _norm_text(t))


def _el_num_text(el) -> str:
    """Get num text from element, handling both namespaced and plain tags."""
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is None:
        return ""
    return "".join(str(_t) for _t in num.itertext()).strip()


def _normalize_nbsp(s: str) -> str:
    """Replace non-breaking spaces (and other space variants) with regular spaces."""
    return re.sub(r'[\xa0\u2009\u202f]', ' ', s)


def _sec_num_key(raw: str) -> str:
    """Normalize section num: replace NBSP, strip '§' suffix, collapse whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'\s*§.*', '', _normalize_nbsp(raw))).strip()


def _ch_num_key(raw: str) -> str:
    """Normalize chapter num: replace NBSP, strip ' luku' suffix."""
    return _normalize_nbsp(raw).replace(" luku", "").strip()


def _extract_replay_sections(replay_root) -> Dict[str, str]:
    """Extract {address → text} matching the gold master key format.

    Matches the logic in scripts/verify_gold_independent.py:
      chapters present → 'chapter:{ch}/section:{sec}'
      no chapters      → 'section:{sec}'
    """

    def _sec_text(sec) -> str:
        return " ".join(str(_t) for _t in sec.itertext()).strip()

    sections: Dict[str, str] = {}
    chapters = replay_root.xpath(".//*[local-name()='chapter']")

    if chapters:
        for ch in chapters:
            ch_raw = _el_num_text(ch)
            ch_num = _ch_num_key(ch_raw)
            for sec in ch.findall("{*}section") or ch.findall("section"):
                sec_raw = _el_num_text(sec)
                sec_num = _sec_num_key(sec_raw)
                if not sec_num:
                    continue
                key = f"chapter:{ch_num}/section:{sec_num}"
                if key not in sections:
                    sections[key] = _sec_text(sec)
    else:
        for sec in replay_root.xpath(".//*[local-name()='section']"):
            sec_raw = _el_num_text(sec)
            sec_num = _sec_num_key(sec_raw)
            if not sec_num:
                continue
            key = f"section:{sec_num}"
            if key not in sections:
                sections[key] = _sec_text(sec)

    return sections


def _normalize_address(address: str) -> str:
    """Normalize a gold address to match _extract_replay_sections keys.

    Gold addresses are already in 'chapter:{num}/section:{num}' format
    matching the extraction logic, so return as-is after stripping whitespace.
    """
    return address.strip()


def _cmd_verify(sid: str, mode: Literal["finlex_oracle", "legal_pit"]) -> None:
    manifest = _load_manifest()
    statutes_list = _get_statutes_list(manifest)
    entry = next((s for s in statutes_list if s.get("statute_id") == sid), None)

    if entry is None:
        print(f"  {sid}: not in gold master.")
        sys.exit(1)

    gold_file = entry.get("file")
    if not gold_file:
        print(f"  {sid}: no per-statute file in manifest (old format).")
        sys.exit(1)

    gold_path = _gold_dir() / gold_file
    if not gold_path.exists():
        print(f"  {sid}: gold file missing: {gold_path}")
        sys.exit(1)

    with open(gold_path) as f:
        statute_data = json.load(f)

    gold_provisions: Dict[str, dict] = statute_data.get("provisions", {})
    gold_date = statute_data.get("verified_date", "?")[:10]

    print(f"Verifying {sid} against gold ({gold_date}, {len(gold_provisions)} provisions)...")

    # Re-run replay
    from lawvm.finland.grafter import replay_xml
    master = replay_xml(sid, mode=mode, quiet=True)
    from lawvm.tools.diff import _extract_sections_ir
    from lawvm.core.ir_helpers import irnode_to_text
    replay_secs_ir = _extract_sections_ir(master.ir)
    replay_secs = {k: irnode_to_text(v) for k, v in replay_secs_ir.items()}

    # Compare each gold provision to current replay
    regressions: List[Tuple[str, float, float]] = []  # tier-2 provisions that changed
    anomaly_changes: List[Tuple[str, float]] = []  # tier-1 provisions that changed
    missing: List[str] = []
    missing_known: List[str] = []  # missing but tier-1
    stable_count = 0
    known_bugs: List[str] = []

    for address, gold_prov in sorted(gold_provisions.items()):
        gold_text = gold_prov.get("text", "")
        gold_tier = gold_prov.get("tier", 2)

        if gold_tier == 1:
            known_bugs.append(address)

        key = _normalize_address(address)
        if key not in replay_secs:
            if gold_tier == 1:
                missing_known.append(address)
            else:
                missing.append(address)
            continue

        cur_text = replay_secs[key]
        sim = Levenshtein.ratio(_clean_text(gold_text), _clean_text(cur_text))

        if sim >= 0.999:
            stable_count += 1
        elif gold_tier == 1:
            # Known anomaly changed — informational, not automatically a regression.
            # The gold stored the old (possibly buggy) replay text; a change here may
            # mean the bug was fixed.
            anomaly_changes.append((address, sim))
        else:
            # Tier-2 confirmed-correct provision changed — genuine regression.
            regressions.append((address, 1.0, sim))

    tier2_total = sum(1 for p in gold_provisions.values() if p.get("tier", 2) == 2)
    print(f"  {sid}: {stable_count}/{tier2_total} tier-2 stable  "
          f"{len(regressions)} regressions  "
          f"{len(missing)} tier-2 missing  "
          f"{len(known_bugs)} known anomalies")

    if known_bugs:
        print("  Known anomalies (tier 1 — expected deviations):")
        for addr in known_bugs:
            note = gold_provisions[addr].get("note", "")[:80]
            print(f"    {addr}: {note}")

    if anomaly_changes:
        print("  Tier-1 anomaly text changed (investigate — may be fix or regression):")
        for addr, sim in sorted(anomaly_changes):
            note = gold_provisions[addr].get("note", "")[:50]
            print(f"    {addr}  now={sim:.1%}  note: {note}")

    if missing_known:
        print(f"  Tier-1 anomalies missing from replay: {missing_known[:3]}")

    if regressions:
        print(f"\n  *** TIER-2 REGRESSIONS DETECTED ({len(regressions)}) — STOP AND INVESTIGATE ***")
        for addr, gold_s, cur_s in sorted(regressions):
            print(f"    {addr}  gold≈100%  current={cur_s:.1%}")
        return

    if missing:
        print(f"  *** TIER-2 MISSING ({len(missing)}) — STOP AND INVESTIGATE ***")
        for addr in missing[:5]:
            print(f"    {addr}")
        return

    print("  OK — all tier-2 provisions stable.")


def _sentinel_csv_path() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent / "data" / "finland" / "strict_sentinel.csv"


def _cmd_verify_strict(sid: Optional[str], mode: Literal["finlex_oracle", "legal_pit"]) -> None:
    """Check strictness for gold statutes via Finland's native facade.

    If sid is given, check only that statute.  Otherwise check all gold statutes.
    Saves the sentinel list (gold statutes that pass strict) to
    data/finland/strict_sentinel.csv.  Exits non-zero if any statute that
    previously passed strict now fails (regression detection for CI).
    """
    from collections import Counter

    from lawvm.finland.compile import compile_fi_facade

    manifest = _load_manifest()
    statutes_list = _get_statutes_list(manifest)

    if sid is not None:
        # Single-statute mode — statute must be in gold master
        entry = next((s for s in statutes_list if s.get("statute_id") == sid), None)
        if entry is None:
            print(f"  {sid}: not in gold master.", file=sys.stderr)
            sys.exit(1)
        targets = [sid]
    else:
        targets = [s.get("statute_id", "") for s in statutes_list if s.get("statute_id")]

    if not targets:
        print("Gold master is empty — nothing to check.")
        return

    print("=== Gold Strict Sentinel ===")
    print(f"Checking {len(targets)} gold statute(s) with compile_fi_facade ...\n")

    results: List[Tuple[str, List[str]]] = []  # (sid, fail_reasons)
    errors: List[str] = []

    for target_sid in targets:
        try:
            facade = compile_fi_facade(target_sid, replay_mode=mode)
            blockers = list(facade.to_wire_artifact().status.blockers or [])
            results.append((target_sid, blockers))
        except Exception as exc:
            errors.append(f"{target_sid}: {exc}")
            results.append((target_sid, [f"compile_error: {exc}"]))

    passing_statutes = [(s, reasons) for s, reasons in results if not reasons]
    failing_statutes = [(s, reasons) for s, reasons in results if reasons]

    total = len(results)
    n_pass = len(passing_statutes)
    n_fail = len(failing_statutes)

    print(f"Total gold statutes:  {total}")
    print(f"Strict pass:          {n_pass} ({n_pass / total:.1%})")
    print(f"Strict fail:          {n_fail} ({n_fail / total:.1%})")

    if errors:
        print(f"\nCompile errors ({len(errors)}):")
        for e in errors:
            print(f"  {e}")

    # Aggregate fail reasons across all failing statutes
    reason_counts: Counter = Counter()
    for _, reasons in failing_statutes:
        for r in reasons:
            reason_counts[r] += 1

    if reason_counts:
        print("\nStrict failures by reason:")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"  {reason:<45} {count}")

    # Sentinel statutes: strict pass + in gold master
    print("\nSentinel statutes (strict + gold):")
    if passing_statutes:
        for s, _ in sorted(passing_statutes):
            print(f"  {s:<20}  strict  gold")
    else:
        print("  (none)")

    # Regression detection: load previous sentinel list BEFORE overwriting
    sentinel_path = _sentinel_csv_path()
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    old_sentinel: set = set()
    if sentinel_path.exists():
        try:
            with open(sentinel_path) as f_old:
                old_sentinel = {
                    line.strip()
                    for line in f_old
                    if line.strip() and line.strip() != "statute_id"
                }
        except OSError:
            pass

    # Save new sentinel CSV
    with open(sentinel_path, "w") as f:
        f.write("statute_id\n")
        for s, _ in sorted(passing_statutes):
            f.write(f"{s}\n")
    print(f"\nSentinel list saved to {sentinel_path}  ({n_pass} entries)")

    new_sentinel = {s for s, _ in passing_statutes}
    regressions = sorted(old_sentinel - new_sentinel)

    if regressions:
        print(f"\n*** STRICT REGRESSION DETECTED ({len(regressions)} statutes) ***")
        print("The following statutes passed strict previously but now fail:")
        for r in regressions:
            fail_reasons = next(
                (reasons for s, reasons in results if s == r and reasons), []
            )
            print(f"  {r}  reasons: {', '.join(fail_reasons) or '(unknown)'}")
        sys.exit(1)
    elif sid is None:
        print("\nNo strict regressions vs previous sentinel.")


def _cmd_export(manifest: dict, out_path: Optional[str]) -> None:
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
        print(f"Exported to {out_path}")
    else:
        print(text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    subcommand = getattr(args, "gold_command", None)
    mode = getattr(args, "mode", "finlex_oracle")
    verbose = getattr(args, "verbose", False)

    if subcommand == "status":
        manifest = _load_manifest()
        _cmd_status(manifest, verbose)

    elif subcommand == "promote":
        sid = args.statute_id
        forced_tier = getattr(args, "tier", None)
        _cmd_promote(sid, mode, forced_tier)

    elif subcommand == "verify":
        sid = getattr(args, "statute_id", None)
        strict = getattr(args, "strict", False)
        if strict:
            _cmd_verify_strict(sid, mode)
        else:
            if sid is None:
                print(
                    "ERROR: statute_id is required for verify without --strict",
                    file=sys.stderr,
                )
                sys.exit(1)
            _cmd_verify(sid, mode)

    elif subcommand == "export":
        manifest = _load_manifest()
        out = getattr(args, "output", None)
        _cmd_export(manifest, out)

    else:
        print("ERROR: provide a gold subcommand: status | promote | verify | export",
              file=sys.stderr)
        sys.exit(1)
