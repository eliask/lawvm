"""lawvm ee-blame — per-provision blame + per-amendment diff matrix for Estonia PIT.

Like 'git blame' for provisions, extended with a diff matrix showing which
provisions each amendment touched across the PIT chain.

Usage:
    lawvm ee-blame <aktViide> --as-of YYYY-MM-DD
    lawvm ee-blame 131052018017 --as-of 2019-03-23
    lawvm ee-blame 131052018017 --as-of 2019-03-23 --matrix    # per-amendment diff table
    lawvm ee-blame 131052018017 --as-of 2019-03-23 --verbose
    lawvm ee-blame 131052018017 --as-of 2019-03-23 --archive .tmp/rt.db
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    import argparse

from dataclasses import replace

from lawvm.core.ir import IRNode, LegalOperation, OperationSource


# ---------------------------------------------------------------------------
# Provision address walker
# ---------------------------------------------------------------------------

def _walk_provisions(node: IRNode, path: tuple = ()) -> List[tuple]:
    """Walk the IRNode tree, yielding (address_tuple, node) for leaf provisions."""
    results = []
    current_path = path + ((node.kind, node.label or ""),) if node.label else path
    if node.kind in ("section", "subsection", "item") and node.label:
        results.append((current_path, node))
    for child in node.children:
        results.extend(_walk_provisions(child, current_path))
    return results


def _addr_str(path: tuple) -> str:
    """Format address tuple as 'chapter:7/section:50/subsection:1'."""
    return "/".join(f"{k}:{v}" for k, v in path if k not in ("body",))


def _addr_key(path: tuple) -> str:
    """Short key for blame_map lookup (matches what apply_ee_ops records)."""
    return "/".join(f"{k}:{v}" for k, v in path if k not in ("body", "chapter", "division", "part"))


# ---------------------------------------------------------------------------
# Blame runner
# ---------------------------------------------------------------------------

def run_ee_blame(
    base_id: str,
    as_of: str,
    archive=None,
    verbose: bool = False,
    show_matrix: bool = False,
) -> None:
    """Run Estonia PIT blame: annotate provisions with last-modifying amendment."""
    from lawvm.estonia.grafter import apply_ee_ops, parse_ee_amendment_ops
    from lawvm.estonia.fetch import (
        extract_amendment_refs, extract_grupi_id,
        extract_tekstiliik, fetch_rt_xml, get_oracle_aktviide_for_pit,
        open_rt_archive,
    )
    from lawvm.estonia.grafter import parse_ee_statute

    _archive = archive or open_rt_archive()

    # ── Load base ─────────────────────────────────────────────────────────────
    from pathlib import Path
    if Path(base_id).suffix == ".xml" or "/" in base_id:
        base_xml = Path(base_id).read_bytes()
    else:
        base_xml = fetch_rt_xml(base_id, _archive)
    base = parse_ee_statute(base_xml, f"ee/{base_id}")

    # ── Discover oracle and intermediate amendments (same logic as replay.py) ─
    grupi_id = extract_grupi_id(base_xml)
    oracle_id: Optional[str] = None
    if grupi_id:
        oracle_id = get_oracle_aktviide_for_pit(grupi_id, as_of, _archive)

    tekstiliik = extract_tekstiliik(base_xml)
    base_is_consolidated = (tekstiliik == "terviktekst")
    base_refs = extract_amendment_refs(base_xml)
    base_aids = {r.aktViide for r in base_refs}

    to_apply: List = []
    if base_is_consolidated and oracle_id and oracle_id != base_id:
        try:
            oracle_xml_diff = fetch_rt_xml(oracle_id, _archive)
            oracle_refs = extract_amendment_refs(oracle_xml_diff)
        except Exception:
            oracle_refs = []
        refs_by_id = {r.aktViide: r for r in oracle_refs}
        new_aids = {r.aktViide for r in oracle_refs} - base_aids
        to_apply = sorted(
            [refs_by_id[a] for a in new_aids if a in refs_by_id
             and refs_by_id[a].joustumine <= as_of],
            key=lambda r: r.joustumine,
        )
    elif base_is_consolidated:
        # Base is already the oracle for this date — nothing new to apply.
        to_apply = []
    else:
        to_apply = sorted(
            [r for r in base_refs
             if r.joustumine and r.joustumine <= as_of],
            key=lambda r: r.joustumine,
        )

    # ── Fetch + parse all amendment ops ──────────────────────────────────────
    all_ops: List[LegalOperation] = []
    amend_ops_by_id: Dict[str, List[LegalOperation]] = {}
    global_seq = 1

    for ref in to_apply:
        try:
            amend_xml = fetch_rt_xml(ref.aktViide, _archive)
        except Exception as e:
            if verbose:
                print(f"  SKIP {ref.aktViide}: fetch failed ({e})", file=sys.stderr)
            continue
        try:
            ops = parse_ee_amendment_ops(amend_xml, f"ee/{ref.aktViide}",
                                         target_title=base.title)
        except Exception as e:
            if verbose:
                print(f"  SKIP {ref.aktViide}: parse failed ({e})", file=sys.stderr)
            continue
        ops = [
            replace(
                op,
                source=OperationSource(
                    statute_id=f"ee/{ref.aktViide}",
                    title=op.source.title if op.source else "",
                    enacted=ref.passed,
                    effective=ref.joustumine,
                    raw_text=op.source.raw_text if op.source else "",
                ),
                sequence=global_seq + idx,
            )
            for idx, op in enumerate(ops)
        ]
        global_seq += len(ops)
        all_ops.extend(ops)
        amend_ops_by_id[ref.aktViide] = ops

    # ── Apply with blame tracking ─────────────────────────────────────────────
    blame_map: dict = {}
    replayed = apply_ee_ops(base, all_ops, blame_map=blame_map)

    # ── Output: per-provision blame ───────────────────────────────────────────
    print(f"\n=== EE Blame: {base_id}  as-of: {as_of} ===")
    print(f"  base    : {base.title[:70]}")
    print(f"  oracle  : {oracle_id or '(none)'}")
    print(f"  amendments applied: {len(to_apply)}")
    print(f"  ops     : {len(all_ops)}")
    print()

    # Walk provisions in the replayed statute
    provisions = _walk_provisions(replayed.body)

    col_w = 30
    unblamed = []
    blamed = []

    for path, node in provisions:
        if node.kind != "section":
            continue  # blame at section level only for readability
        addr_display = _addr_str(path)
        key_short = _addr_key(path)
        op = blame_map.get(key_short)
        if op is None:
            unblamed.append((addr_display, node))
        else:
            src = op.source
            src_id = src.statute_id.replace("ee/", "") if src else "?"
            eff = src.effective if src else "?"
            title = (src.title or "")[:40]
            blamed.append((addr_display, node, src_id, eff, title, op.sequence, op.action.upper()))

    for addr, node, src_id, eff, title, seq, action in blamed:
        print(f"  {addr:<{col_w}}  {src_id}  [{seq:>4}] {action:<12}  eff={eff}")
        if verbose and node.text:
            print(f"    {node.text[:80]}")

    if unblamed:
        print()
        print("  (unmodified — base statute text, no op compiled:)")
        for addr, node in unblamed:
            print(f"    {addr}")

    print()
    print(f"  {len(blamed)} sections annotated, {len(unblamed)} from base")

    # ── Optional: per-amendment diff matrix ──────────────────────────────────
    if show_matrix and amend_ops_by_id:
        print()
        print("=== Per-amendment change matrix ===")
        print()
        for aid, ops in amend_ops_by_id.items():
            if not ops:
                continue
            ref_obj = next((r for r in to_apply if r.aktViide == aid), None)
            eff = ref_obj.joustumine if ref_obj else "?"
            print(f"  Amendment {aid}  (effective {eff})  [{len(ops)} ops]")
            # Group by action type
            by_action: Dict[str, List[str]] = defaultdict(list)
            for op in ops:
                if op.target.path:
                    addr = "/".join(f"{k}:{v}" for k, v in op.target.path)
                    by_action[op.action.value].append(addr)
            for action, addrs in sorted(by_action.items()):
                print(f"    {action.upper()}: {len(addrs)} provisions")
                if verbose:
                    for a in addrs[:10]:
                        print(f"      {a}")
                    if len(addrs) > 10:
                        print(f"      ... ({len(addrs)-10} more)")
            print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: "argparse.Namespace") -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from pathlib import Path

    archive = None
    if getattr(args, "archive", None):
        archive = open_rt_archive(Path(args.archive))

    run_ee_blame(
        base_id=args.base_id,
        as_of=args.as_of,
        archive=archive,
        verbose=getattr(args, "verbose", False),
        show_matrix=getattr(args, "matrix", False),
    )
