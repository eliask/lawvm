"""lawvm uk-replay -- UK archive-backed amendment replay with timeline integration.

Replays UK legislation amendments against an enacted base loaded from the
Farchive DB, compares against an archive-backed oracle (current or PIT
when present in the archive), and optionally compiles provision timelines.

Usage:
    lawvm uk-replay ukpga/1998/42
    lawvm uk-replay ukpga/1998/42 --pit-date 2020-01-01
    lawvm uk-replay ukpga/1998/42 --enacted-only
    lawvm uk-replay ukpga/1998/42 --verbose
    lawvm uk-replay ukpga/1998/42 --fetch-missing   # pre-fetch missing affecting act XMLs
    lawvm uk-replay ukpga/1998/42 --timeline        # compile ops-first timelines + show summary
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence, Set

if TYPE_CHECKING:
    import argparse
    from lawvm.core.ir import IRStatute

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import is_zombie

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"


def _get_all_eids(
    nodes: Sequence[IRNode],
    pit_date: Optional[str] = None,
) -> Set[str]:
    """Collect all eId/id attributes from a list of IRNode trees."""
    eids: Set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            eids.add(eid)
        eids.update(_get_all_eids(n.children, pit_date=pit_date))
    return eids


def _archive_url_for_statute(statute_id: str, *, pit_date: Optional[str], enacted: bool) -> str:
    if enacted:
        return f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
    if pit_date:
        return f"{_LEG_BASE}/{statute_id}/{pit_date}/data.xml"
    return f"{_LEG_BASE}/{statute_id}/data.xml"


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_bench_comparison,
        is_core_uk_comparison,
        normalize_uk_replay_compare_eids,
    )
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.uk_amendment_replay import load_effects_for_statute_from_archive
    from lawvm.core.timeline import compile_timelines, materialize_pit
    from lawvm.core.timeline_consistency import ingest_uk_snapshots
    from lawvm.tools.replay_payloads import build_uk_replay_payload

    statute_id: str = args.statute_id
    pit_date: Optional[str] = getattr(args, "pit_date", None)
    enacted_only: bool = getattr(args, "enacted_only", False)
    verbose: bool = getattr(args, "verbose", False)
    fetch_missing: bool = getattr(args, "fetch_missing", False)
    as_json: bool = getattr(args, "json", False)
    db_arg: Optional[str] = getattr(args, "db", None)
    use_timeline: bool = getattr(args, "timeline", False)
    _out = (lambda *a, **k: None) if as_json else print

    # ── 0. Pre-fetch missing affecting act XMLs (optional) ─────────────────
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    if fetch_missing:
        from lawvm.uk_legislation.uk_prefetch import fetch_missing_for_statute

    n_provisions = 0
    timelines = {}
    n_ops = 0
    similarity: Optional[float] = None
    replay_adjudications: list = []

    with Farchive(db_path) as archive:
        if fetch_missing:
            fetched, cached, errors = fetch_missing_for_statute(
                statute_id,
                archive,
                delay=0.8,
                verbose=verbose,
            )
            print(
                f"Pre-fetch: {fetched} fetched, {cached} already cached, {errors} errors",
                file=sys.stderr,
            )

        enacted_url = _archive_url_for_statute(statute_id, pit_date=pit_date, enacted=True)
        enacted_bytes = archive.get(enacted_url)
        if not enacted_bytes or len(enacted_bytes) < 100:
            print(f"error: enacted XML missing from archive for {enacted_url}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            print(f"Loading base IR from archive: {enacted_url}", file=sys.stderr)
        base_ir = parse_uk_statute_ir_bytes(
            enacted_bytes,
            statute_id=statute_id,
            version_label="enacted",
            pit_date=pit_date,
            source_path=enacted_url,
        )
        base_eids = _get_all_eids([base_ir.body], pit_date=pit_date)
        for schedule in base_ir.supplements:
            base_eids.update(_get_all_eids([schedule], pit_date=pit_date))
        _out(f"Base EIDs: {len(base_eids)}")

        oracle_url = _archive_url_for_statute(statute_id, pit_date=pit_date, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        eid_map: dict[str, str] = {}
        text_map: dict[str, str] = {}
        current_ir = None
        current_eids: Set[str] = set()
        n_effects = len(load_effects_for_statute_from_archive(statute_id, archive))
        comparison_class = ""
        core_benchmark = False

        if oracle_bytes and len(oracle_bytes) >= 100:
            if verbose:
                print(
                    f"Extracting oracle EID map from archive: {oracle_url} (PIT: {pit_date or 'latest'})",
                    file=sys.stderr,
                )
            oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=pit_date)
            eid_map = oracle_data.get("eid_map", {})
            text_map = oracle_data.get("text_map", {})
            current_eids = set(eid_map.values())
            if verbose:
                print(f"Oracle EID map entries: {len(eid_map)}", file=sys.stderr)
            current_ir = parse_uk_statute_ir_bytes(
                oracle_bytes,
                statute_id=statute_id,
                version_label="oracle",
                pit_date=pit_date,
                source_path=oracle_url,
            )
            comparison_class = classify_uk_bench_comparison(
                n_enacted_eids=len(base_eids),
                n_oracle_eids=len(current_eids),
                n_effects=n_effects,
                raw_score=(len(base_eids & current_eids) / max(len(base_eids), len(current_eids), 1)),
            )
            core_benchmark = is_core_uk_comparison(comparison_class)

        # ── 3. Replay ─────────────────────────────────────────────────────
        if enacted_only:
            _out("\n--- Baseline mode: enacted vs enacted ---")
            replayed_ir = base_ir
            lo_ops_out = None
        else:
            pipeline_cls = getattr(uk_replay_module, "UKReplayPipeline")
            pipeline = pipeline_cls(_REPO_ROOT)
            ops = pipeline.compile_ops_for_statute(
                statute_id,
                pit_date=pit_date,
                archive=archive,
            )
            n_ops = len(ops)
            _out(f"Compiled {n_ops} operations")
            if verbose:
                for op in ops:
                    kind = op.payload.kind if op.payload is not None else "none"
                    print(f"  Op {op.op_id}: {op.action} {op.target} -> IR kind: {kind}", file=sys.stderr)

            lo_ops_out = [] if use_timeline else None
            replayed_ir = pipeline.apply_ops(
                base_ir,
                ops,
                eid_map=eid_map,
                text_map=text_map,
                verbose=verbose,
                lo_ops_out=lo_ops_out,
                adjudications_out=replay_adjudications,
            )

        # ── 4. EID similarity score ───────────────────────────────────────
        replayed_eids = _get_all_eids([replayed_ir.body], pit_date=pit_date)
        for schedule in replayed_ir.supplements:
            replayed_eids.update(_get_all_eids([schedule], pit_date=pit_date))
        _out(f"Replayed EIDs: {len(replayed_eids)}")

        if current_ir is not None:
            _out(f"Oracle EIDs: {len(current_eids)}")
            replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                replayed_eids,
                current_eids,
            )
            common = replay_compare_eids & oracle_compare_eids
            similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)
            _out(f"Full EID Similarity: {similarity:.1%}")
            if comparison_class:
                _out(f"Comparison class: {comparison_class}  core={'yes' if core_benchmark else 'no'}")
            only_in_replayed = replay_compare_eids - oracle_compare_eids
            only_in_oracle = oracle_compare_eids - replay_compare_eids
            if only_in_replayed:
                sample = sorted(only_in_replayed)[:10]
                _out(f"Only in replayed ({len(only_in_replayed)}): {sample}")
            if only_in_oracle:
                sample = sorted(only_in_oracle)[:10]
                _out(f"Only in oracle ({len(only_in_oracle)}): {sample}")
        else:
            _out(f"Note: no oracle XML in archive for {oracle_url}.")

    # ── 5. Timeline compilation ───────────────────────────────────────────
    # Two paths:
    #
    # Default (states-first / ingest_uk_snapshots):
    #   Build timelines from enacted + replayed snapshots. Simple structural
    #   diff — any provision that changed between the two snapshots gets a new
    #   version. Accurate for "what changed overall" but loses per-op granularity.
    #
    # --timeline (ops-first / compile_timelines):
    #   Use the lo_ops_out snapshots collected during apply_ops. Each structural
    #   op emits a top-section snapshot immediately after application, so
    #   compile_timelines sees fine-grained per-op versions with proper source
    #   provenance (affecting act ID + effective date). Mirrors the Finland path.

    parts = statute_id.split("/")
    enacted_year = parts[1] if len(parts) >= 3 else "1900"
    enacted_date = f"{enacted_year}-01-01"

    if use_timeline and lo_ops_out is not None and not enacted_only:
        # Ops-first path: compile_timelines from section snapshots
        temporal_events = uk_replay_module._uk_temporal_events_from_ops(  # type: ignore[attr-defined]
            lo_ops_out,
            target_statute=statute_id,
        )
        timelines = compile_timelines(
            base_ir,
            lo_ops_out,
            base_date=enacted_date,
            temporal_events=temporal_events,
        )
        n_provisions = len(timelines)
        n_versions = sum(len(tl.versions) for tl in timelines.values())
        n_snapshots = len(lo_ops_out)
        _out(f"\n[ops-first] Snapshots collected: {n_snapshots}")
        _out(f"[ops-first] Timelines: {n_provisions} provisions, {n_versions} total versions")

        # Per-provision version count summary
        multi_version = {addr: len(tl.versions) for addr, tl in timelines.items() if len(tl.versions) > 1}
        if multi_version:
            # Sort by version count descending, print top 10
            top = sorted(multi_version.items(), key=lambda kv: -kv[1])[:10]
            _out(f"[ops-first] Provisions with multiple versions (top {len(top)}):")
            for addr, count in top:
                addr_str = "/".join(f"{k}:{lbl}" for k, lbl in addr.path)
                _out(f"  {addr_str}: {count} versions")
    else:
        # States-first path (default): ingest_uk_snapshots
        snapshots: dict[str, "IRStatute"] = {}
        snapshots[enacted_date] = base_ir

        if not enacted_only:
            # Use today or pit_date as the replayed date
            if pit_date:
                replay_date = pit_date
            else:
                import datetime

                replay_date = datetime.date.today().isoformat()
            snapshots[replay_date] = replayed_ir

        timelines = ingest_uk_snapshots(statute_id, snapshots)
        n_provisions = len(timelines)
        n_versions = sum(len(tl.versions) for tl in timelines.values())
        _out(f"\nTimelines: {n_provisions} provisions, {n_versions} total versions")

    # Materialize PIT if date given
    if pit_date and timelines:
        pit_statute = materialize_pit(timelines, pit_date, base=base_ir)
        pit_eids = _get_all_eids([pit_statute.body], pit_date=pit_date)
        _out(f"Materialized PIT ({pit_date}): {len(pit_eids)} EIDs in body")
    else:
        pit_eids = None

    if as_json:
        payload = build_uk_replay_payload(
            statute_id=statute_id,
            pit_date=pit_date,
            enacted_only=enacted_only,
            db_path=str(db_path),
            n_effects=n_effects,
            n_ops=n_ops,
            similarity=similarity,
            comparison_class=comparison_class or None,
            oracle_available=current_ir is not None,
            n_provisions=n_provisions,
            n_versions=n_versions if timelines else None,
            pit_materialized_eids=len(pit_eids) if pit_eids is not None else None,
            timeline_mode="ops_first" if use_timeline and not enacted_only else "states_first",
            adjudications=replay_adjudications,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # ── 6. Summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"Statute:    {statute_id}")
    print(f"Mode:       {'enacted-only' if enacted_only else 'full replay'}")
    if pit_date:
        print(f"PIT date:   {pit_date}")
    print(f"Ops:        {n_ops}")
    if similarity is not None:
        print(f"EID score:  {similarity:.1%}")
    print(f"Timelines:  {n_provisions} provisions")
    print(f"{'=' * 60}")
