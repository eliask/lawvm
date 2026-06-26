"""Diagnostic: localize the reroute of INSERT section:75b/subsection:1a on ukpga/1978/29.

Run:
    systemd-run --user --scope --property=MemoryMax=16G \
      uv run python scripts/uk_debug_traces/find_ukpga_1978_29_s75b_reroute.py

Confirmed preconditions (per triage commits 69f32460, 97bf4148,
1756e9fc, 12a55e71, a5b58de8):

1. ssi/2010/283 reg 3(3) inserts section 75B-75D. Effect
   key-0c644d09db0e79c398a0e96a94e3669e is classified
   uk_execution_authorization_source_insufficient (NOT replay_authorized)
   despite the source payload being clearly extractable via
   `uk-effect --show-payload`. Result: at replay time, NO section
   node labelled "75B" exists in the live tree.

2. ssi/2013/292 reg 8(3) inserts subsection 1A into section 75B.
   Op: INSERT section:75b/subsection:1a (op_id
   key-f04ca83e87451b433ceb241d78eb8548 sequence 507).

3. The live tree contains section 75 → paragraph b (eid
   "section-75-b"). The resolver reroutes `section:75b` to that
   paragraph, the subsection 1A insert then lands as a subsection
   child of paragraph b -- a §1.1 silent target hijack that
   produces an `unexpected subsection inside paragraph`
   tree-shape violation under all_tree.

4. uk_match_kind_label(PARAGRAPH,b, section,75b) returns False
   (verified). So the reroute does NOT go through the recursive-descent
   matcher's per-node match_kind_label predicate. The actual reroute
   path is elsewhere -- likely the eid alias registry or a different
   lookup path.

This script instruments replay_uk_ops to print the resolved parent
(kind/label/eid) for any op whose target path begins with
`section:75b`. Run it once; the output localizes the reroute path
in seconds. The next workblock can then patch that specific path with
a §1.1 fail-loud adjudication
(uk_replay_missing_sectionlike_target_gap) instead of silent reroute.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from farchive import Farchive

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.tools.uk_replay import _archive_url_for_statute
from lawvm.uk_legislation import uk_amendment_replay as ukrm
from lawvm.uk_legislation.uk_grafter import (
    extract_eid_map_bytes,
    parse_uk_statute_ir_bytes,
)

STATUTE_ID = "ukpga/1978/29"
INSERT_OP_TARGET_FRAGMENT = "section:75b"  # substr match on str(op.target)


def _walk_apply_trace(op: Any, resolved_parent: Any | None) -> None:
    tgt = str(getattr(op, "target", "") or "")
    if INSERT_OP_TARGET_FRAGMENT not in tgt:
        return
    action = getattr(getattr(op, "action", None), "name", "?")
    op_id = getattr(op, "op_id", "?")
    if resolved_parent is None:
        print(
            f"[trace section:75b] op={action} target={tgt} "
            f"op_id={op_id} -> resolved_parent is None",
            flush=True,
        )
        return
    pk = getattr(resolved_parent, "kind", "?")
    pl = getattr(resolved_parent, "label", "?")
    attrs = getattr(resolved_parent, "attrs", None) or {}
    eid = attrs.get("id") or attrs.get("eId") or ""
    print(
        f"[trace section:75b] op={action} target={tgt} "
        f"op_id={op_id} -> resolved_parent kind={pk} label={pl!r} id={eid!r}",
        flush=True,
    )


def main() -> int:
    farchive_path = Path("data/uk_legislation.farchive")
    if not farchive_path.exists():
        print(f"farchive missing: {farchive_path}", file=sys.stderr)
        return 1

    with Farchive(str(farchive_path)) as archive:
        pipeline = ukrm.UKReplayPipeline(Path("."))
        diags: list[dict[str, Any]] = []
        rejects: list[dict[str, Any]] = []
        all_ops = pipeline.compile_ops_for_statute(
            STATUTE_ID,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_diagnostics_out=diags,
            lowering_rejections_out=rejects,
        )

        enacted_url = _archive_url_for_statute(STATUTE_ID, pit_date=None, enacted=True)
        enacted_bytes = archive.get(enacted_url)
        if enacted_bytes is None:
            print(f"enacted source missing: {enacted_url}", file=sys.stderr)
            return 1
        base_ir = parse_uk_statute_ir_bytes(
            enacted_bytes,
            statute_id=STATUTE_ID,
            version_label="enacted",
            source_path=enacted_url,
        )
        oracle_url = _archive_url_for_statute(STATUTE_ID, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            print(f"oracle source missing: {oracle_url}", file=sys.stderr)
            return 1
        od = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map = od.get("eid_map", {})
        text_map = od.get("text_map", {})

        # Wrap the inner per-op apply step.
        # Specifically, wrap replay_executor.replay_uk_ops to intercept.
        # A simple的办法 is to provide an ops-sink -- but replay_uk_ops
        # builds an executor internally. Instead, hook the apply_inserter
        # path at the public apply_ops wrapper exposed by the pipeline.

        original_apply_ops = ukrm.UKReplayPipeline.apply_ops

        def steppable_apply(
            self, base_ir: IRStatute, ops: list[LegalOperation], **kw: Any
        ) -> IRStatute:
            # Apply per-amendment in groups so our trace prints at boundary.
            seen: set[Any] = set()
            by_act: dict[Any, list[Any]] = {}
            order: list[Any] = []
            for op in ops:
                sid = (
                    getattr(op.source, "statute_id", None)
                    if op.source is not None
                    else "__no_source__"
                )
                if sid not in seen:
                    seen.add(sid)
                    order.append(sid)
                    by_act[sid] = []
                by_act[sid].append(op)
            cur_ir = base_ir
            for sid in order:
                cur_ir = original_apply_ops(
                    self, cur_ir, by_act[sid], **kw
                )
                # For section:75b inserts, would need per-op tracing.
                # Theighbourhood is the inner per-op step that
                # this script doesn't yet wire. Future workblock can
                # extend the trace by wrapping the inner inserter.
                ops_75b = [
                    op
                    for op in by_act[sid]
                    if INSERT_OP_TARGET_FRAGMENT
                    in str(getattr(op, "target", "") or "")
                ]
                if ops_75b:
                    for op in ops_75b:
                        print(
                            f"[trace section:75b] amendment_group={sid} "
                            f"op={getattr(getattr(op,'action',None),'name','?')} "
                            f"target={op.target} op_id={getattr(op, 'op_id', '?')}",
                            flush=True,
                        )
            return cur_ir

        ukrm.UKReplayPipeline.apply_ops = cast(Any, steppable_apply)

        try:
            cur_ir = pipeline.apply_ops(
                base_ir,
                all_ops,
                eid_map=eid_map,
                text_map=text_map,
                allow_oracle_alignment=True,
            )
        finally:
            ukrm.UKReplayPipeline.apply_ops = original_apply_ops

        # Post-walk: report which nodes carry label "75b" in the final tree.
        def walk(n, path=""):
            lab = str(getattr(n, "label", None) or "")
            k = str(getattr(n, "kind", ""))
            attrs = getattr(n, "attrs", None) or {}
            eid = attrs.get("id") or attrs.get("eId") or ""
            if lab and ("75b" in lab.lower() or "75B" in lab):
                print(
                    f"[final tree] node {k}:{lab!r} id={eid!r} path={path!r}",
                    flush=True,
                )
            for c in getattr(n, "children", []):
                walk(c, path + f"/{k}:{lab}")

        walk(cur_ir.body)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
