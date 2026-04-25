#!/usr/bin/env python3
"""Capture pipeline intermediates for per-step error attribution.

Phase 1: non-invasive capture using existing pipeline sinks.
Captures johtolause, PEG ops, body sections, compiled ops, failed ops,
and per-amendment section snapshots — WITHOUT modifying grafter.py.

Usage:
    uv run python scripts/capture_gold.py --corpus .tmp/iteration_corpus_50.csv
    uv run python scripts/capture_gold.py --statutes 2009/953,1959/324
    uv run python scripts/capture_gold.py --stats
"""
import argparse
import csv
import io
import sys
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))


def _capture_one(statute_id: str) -> list:
    """Capture intermediates for one statute using existing pipeline."""
    from lawvm.core.pipeline_capture import AmendmentCapture
    from lawvm.finland.grafter import (
        _resolve_applicable_amendment_records,
        _get_corpus_store,
        get_johtolause,
        _normalize_johtolause_verbs,
        _is_omission_ir,
        _fi_label_postprocessor,
    )
    from lawvm.finland.johtolause import extract_legal_ops
    from lawvm.xml_ingest import xml_to_ir_node
    from lawvm.finland.corrigendum import get_patch_table
    import lxml.etree as etree

    cs = _get_corpus_store()
    records, _, _ = _resolve_applicable_amendment_records(statute_id, "finlex_oracle")
    captures = []

    for rec in records:
        amendment_id = str(rec["statute_id"])
        cap = AmendmentCapture(statute_id=statute_id, amendment_id=amendment_id)
        cap.effective_date = str(rec.get("effective_date", "") or "")
        cap.source_title = str(rec.get("title", ""))

        xml = cs.read_source(amendment_id)
        if xml is None:
            captures.append(cap)
            continue

        xml, _ = get_patch_table().patch_source_xml(xml, amendment_id)

        # Step 1: johtolause
        johto = get_johtolause(xml)
        cap.preamble_raw = johto or ""
        cap.preamble_normalized = _normalize_johtolause_verbs(johto) if johto else ""

        # Check if sec1 fallback would be needed
        from lawvm.finland.grafter import OP_KEYWORDS
        has_op_keywords = any(kw in cap.preamble_normalized.lower() for kw in OP_KEYWORDS)
        if not has_op_keywords and johto and len(johto) < 50:
            cap.used_sec1_fallback = True

        # Step 2: PEG extraction
        if cap.preamble_normalized:
            try:
                legal_ops = extract_legal_ops(cap.preamble_normalized)
                cap.peg_ops = [
                    {"action": op.action, "target": str(op.target), "op_id": op.op_id}
                    for op in legal_ops
                ]
                cap.extraction_path = "peg"
            except Exception:
                cap.extraction_path = "peg_error"

        if not cap.peg_ops and cap.preamble_normalized:
            # Check fallback paths
            from lawvm.finland.grafter import parse_ops_fallback_heuristic, _tree_title
            tree = etree.fromstring(xml)
            fallback = parse_ops_fallback_heuristic(cap.preamble_normalized)
            if fallback:
                cap.peg_ops = [{"action": op.op_type, "section": op.target_section} for op in fallback]
                cap.extraction_path = "fallback_heuristic"
            else:
                from lawvm.finland.grafter import parse_ops_title_fallback
                title = _tree_title(tree)
                title_ops = parse_ops_title_fallback(title)
                if title_ops:
                    cap.peg_ops = [{"action": op.op_type, "section": op.target_section} for op in title_ops]
                    cap.extraction_path = "title_fallback"
                else:
                    cap.extraction_path = "none"

        # Step 3: citation routing (check what the grafter would do)
        if statute_id and amendment_id:
            from lawvm.finland.grafter import _johtolause_references_parent
            citation_johto = _normalize_johtolause_verbs(get_johtolause(xml) or "")
            refs_match = _johtolause_references_parent(citation_johto, statute_id) if citation_johto else True
            cap.citation_match = refs_match
            if not refs_match:
                amendment_id_num = amendment_id.split("/")[1] if "/" in amendment_id else ""
                par_num = statute_id.split("/")[1] if "/" in statute_id else ""
                cap.citation_action = "skip_num_collision" if amendment_id_num == par_num else "skip_citation_mismatch"
            else:
                cap.citation_action = "pass"

        # Step 5: body content
        tree = etree.fromstring(xml)
        body = tree.find(".//{*}body")
        if body is not None:
            body_ir = xml_to_ir_node(body, _fi_label_postprocessor)
            def _collect_secs(node, out):
                for child in node.children:
                    if child.kind == 'section' and child.label:
                        out[child.label] = child
                    elif child.kind in ('chapter', 'part', 'hcontainer'):
                        _collect_secs(child, out)
            secs = {}
            _collect_secs(body_ir, secs)
            cap.body_section_labels = sorted(secs.keys())
            cap.body_has_omissions = {
                label: any(_is_omission_ir(c) for c in sec.children)
                for label, sec in secs.items()
            }

        captures.append(cap)

    return captures


def _capture_one_worker(sid: str) -> list:
    """Worker function for parallel capture. Returns serializable dicts."""
    import io as _io
    old = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        caps = _capture_one(sid)
    except Exception as e:
        sys.stdout = old
        return [{"statute_id": sid, "amendment_id": "ERROR", "error": str(e)[:200]}]
    finally:
        if sys.stdout != old:
            sys.stdout = old
    from dataclasses import asdict
    return [asdict(c) for c in caps]


def main():
    parser = argparse.ArgumentParser(description="Capture pipeline intermediates")
    parser.add_argument("--corpus", metavar="CSV", help="corpus CSV path")
    parser.add_argument("--statutes", default="", help="comma-separated statute IDs")
    parser.add_argument("--stats", action="store_true", help="show capture stats")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--parallel", type=int, default=1, help="worker count")
    args = parser.parse_args()

    from lawvm.core.pipeline_capture import CaptureStore, AmendmentCapture
    store = CaptureStore()

    if args.stats:
        print(store.stats())
        return

    if args.statutes:
        sids = [s.strip() for s in args.statutes.split(",")]
    elif args.corpus:
        with open(args.corpus) as f:
            sids = [r[1].strip() for r in csv.reader(f) if len(r) >= 2 and r[0].isdigit()]
    else:
        print("Specify --corpus or --statutes")
        return

    if args.limit:
        sids = sids[:args.limit]

    total = len(sids)
    all_captures = 0

    if args.parallel > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        done = 0
        with ProcessPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(_capture_one_worker, sid): sid for sid in sids}
            for fut in as_completed(futures):
                sid = futures[fut]
                dicts = fut.result()
                caps = []
                for d in dicts:
                    if "error" in d:
                        continue
                    caps.append(AmendmentCapture(**{
                        k: v for k, v in d.items()
                        if k in AmendmentCapture.__dataclass_fields__
                    }))
                if caps:
                    store.save_batch(caps)
                all_captures += len(caps)
                done += 1
                if done % 200 == 0 or done == total:
                    print(f"[{done}/{total}] latest: {sid} — {len(caps)} amendments")
    else:
        for i, sid in enumerate(sids, 1):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                captures = _capture_one(sid)
            finally:
                sys.stdout = old
            store.save_batch(captures)
            all_captures += len(captures)
            if i % 50 == 0 or i == total:
                print(f"[{i}/{total}] {sid} — {len(captures)} amendments")

    print(f"\nCaptured {all_captures} amendments from {total} statutes")
    print(store.stats())


if __name__ == "__main__":
    main()
