#!/usr/bin/env python3
import sys
import json
from pathlib import Path
from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline
from lawvm.core.ir_helpers import is_zombie


REPO_ROOT = Path(__file__).resolve().parents[1]


def get_all_eids(nodes, pit_date=None):
    eids = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid: eids.add(eid)
        eids.update(get_all_eids(n.children, pit_date=pit_date))
    return eids

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("statute_id", help="e.g. ukpga/1998/42")
    parser.add_argument("--enacted-only", action="store_true", help="Compare enacted vs enacted (baseline)")
    parser.add_argument("--pit-date", help="YYYY-MM-DD for PIT replay and oracle comparison")
    args = parser.parse_args()

    statute_id = args.statute_id
    pit_date = args.pit_date
    repo_root = REPO_ROOT

    # 1. Load Base IR (Enacted)
    base_xml = repo_root / f"uk/data/raw/{statute_id}/enacted/data.xml"
    if not base_xml.exists():
        print(f"Error: Base XML not found at {base_xml}")
        sys.exit(1)

    print(f"--- Loading Base IR: {statute_id} ---")
    base_ir = parse_uk_statute_ir(base_xml, statute_id, pit_date=pit_date)
    base_eids = get_all_eids(base_ir.body.children)
    print(f"Base EIDs: {len(base_eids)}")

    # 2. Oracle Selection / EID Map Extraction
    from lawvm.uk_legislation.uk_grafter import extract_eid_map

    if pit_date:
        current_xml = repo_root / f".tmp/uk_oracle_{statute_id.replace('/','_')}_{pit_date}.xml"
        if not current_xml.exists():
            print(f"Fetching PIT Oracle for {pit_date}...")
            from lawvm.uk_legislation.effects import _download_file
            url = f"https://www.legislation.gov.uk/{statute_id}/{pit_date}/data.xml"
            try:
                _download_file(url, current_xml)
            except Exception as e:
                print(f"Error fetching PIT Oracle: {e}")
                sys.exit(1)
    else:
        current_xml = repo_root / f"uk/data/raw/{statute_id}/current/data.xml"

    eid_map = {}
    text_map = {}
    if current_xml.exists():
        print(f"--- Extracting Oracle EID Map: {current_xml.name} (PIT: {pit_date or 'latest'}) ---")
        oracle_data = extract_eid_map(current_xml, pit_date=pit_date)
        eid_map = oracle_data.get("eid_map", {})

        text_map = oracle_data.get("text_map", {})
        print(f"Extracted {len(eid_map)} mapping entries from Oracle.")

    # 3. Pipeline Orchestration / Replay
    if args.enacted_only:
        print("\n--- Baseline Mode: Enacted vs Enacted ---")
        replayed_ir = base_ir
        # In baseline mode, we compare against itself
        current_xml = base_xml
    else:
        from farchive import Farchive
        db_path = repo_root / "data" / "uk_legislation.farchive"
        archive = Farchive(db_path)
        try:
            pipeline = UKReplayPipeline(repo_root)

            # Compile ops
            print(f"\n--- Compiling Ops for {statute_id} (PIT: {pit_date or 'latest'}) ---")
            ops = pipeline.compile_ops_for_statute(statute_id, pit_date=pit_date, archive=archive)
            print(f"Compiled {len(ops)} operations.")
            for op in ops:
                kind = op.payload.kind if op.payload is not None else "none"
                print(f"  Op {op.op_id}: {op.action} {op.target} -> IR kind: {kind}")

            # 4. Run Replay
            print(f"\n--- Running Replay for {statute_id} ---")
            replayed_ir = pipeline.apply_ops(base_ir, ops, eid_map=eid_map, text_map=text_map)
        finally:
            archive.close()

        # Save for analysis
        slug = statute_id.replace("/", "_")
        out_path = repo_root / f".tmp/uk_replayed_{slug}.json"
        print(f"Saving replayed IR to {out_path}")
        with open(out_path, 'w') as f:
            json.dump(replayed_ir.to_jsonable_dict(), f, indent=2)

    # 4. Inspect result

    # 4. Inspect result
    print("\n--- Verification ---")
    replayed_eids = get_all_eids([replayed_ir.body], pit_date=pit_date)
    for s in replayed_ir.supplements:
        replayed_eids.update(get_all_eids([s], pit_date=pit_date))
    print(f"Replayed EIDs count: {len(replayed_eids)}")

    # Final check: does it match oracle?
    # (current_xml is already set appropriately above)
    if not pit_date and not args.enacted_only:
        current_xml = repo_root / f"uk/data/raw/{statute_id}/current/data.xml"

    if current_xml.exists():
        current_ir = parse_uk_statute_ir(current_xml, statute_id, pit_date=pit_date)
        current_eids = get_all_eids([current_ir.body], pit_date=pit_date)
        for s in current_ir.supplements:
             current_eids.update(get_all_eids([s], pit_date=pit_date))

        print(f"Oracle (Current) EIDs: {len(current_eids)}")
        # Convert to list for sampling
        cur_list = sorted(list(current_eids))
        rep_list = sorted(list(replayed_eids))
        print(f"Sample Oracle EIDs: {cur_list[:5]}")
        print(f"Sample Replayed EIDs: {rep_list[:5]}")

        common = replayed_eids & current_eids
        print(f"Common EIDs ({len(common)}): {sorted(list(common))[:10]}")

        # Use Full EID set similarity for high-precision verification
        replayed_set = replayed_eids
        current_set = current_eids


        common = replayed_set & current_set
        print(f"Common EIDs ({len(common)}): {sorted(list(common))[:10]}...")

        sim = len(common) / max(len(replayed_set), len(current_set), 1)
        print(f"Full EID Similarity: {sim:.1%}")

        only_in_replayed = replayed_set - current_set
        only_in_oracle = current_set - replayed_set

        if only_in_replayed:
            print(f"Only in Replayed (first 10): {sorted(list(only_in_replayed))[:10]}")
        if only_in_oracle:
            print(f"Only in Oracle (first 10): {sorted(list(only_in_oracle))[:10]}")
    else:
        print("Note: No current version found for oracle comparison.")

    print("\nReplay script finished.")

if __name__ == "__main__":
    main()
