"""lawvm ops — list compiled operations with provenance.

Shows all operations compiled during replay, with their source amendment
and target address. Useful for understanding what the pipeline did and
for correlating score changes with specific operations.

Usage:
    lawvm ops <statute_id>                         # all compiled ops
    lawvm ops <statute_id> --source 2017/794       # ops from one amendment
    lawvm ops <statute_id> --target "section:9a"   # ops targeting one provision
    lawvm ops <statute_id> --source 2017/794 --target "section:9"
"""
from __future__ import annotations

import json
import sys
from typing import Any, Literal, Optional

from lawvm.finland.grafter import replay_xml
from lawvm.core.ir import LegalOperation


# ---------------------------------------------------------------------------
# Address formatting
# ---------------------------------------------------------------------------

def _fmt_target(target: dict) -> str:
    """Format an IRTargetRef dict as a human-readable address."""
    container = target.get("container", "?")
    section = target.get("section") or ""
    subsection = target.get("subsection")
    item = target.get("item")
    special = target.get("special")

    if container == "section":
        addr = f"§ {section}"
        if subsection is not None:
            addr += f" mom {subsection}"
        if item:
            addr += f" kohta {item}"
        if special:
            addr += f" ({special})"
    elif container == "chapter":
        addr = f"luku {section}"
        # chapter-level ops may carry subsection = target section number
        inner_sec = target.get("subsection")
        if inner_sec:
            addr += f" / § {inner_sec}"
    elif container == "part":
        addr = f"osa {section}"
    else:
        addr = f"{container}:{section}"
        if subsection:
            addr += f"/{subsection}"

    return addr


def _matches_source(op: dict, source_filter: str) -> bool:
    return op.get("source_statute", "").strip() == source_filter.strip()


def _matches_target(op: dict, target_filter: str) -> bool:
    """Check if op matches a 'kind:label' address filter."""
    if ":" not in target_filter:
        return False
    kind, label = target_filter.split(":", 1)
    kind = kind.strip().lower()
    label = label.strip().lower()

    target = op.get("target", {})
    container = target.get("container", "").lower()
    section = (target.get("section") or "").lower().replace(" ", "").replace("§", "")
    label_norm = label.replace(" ", "").replace("§", "")

    return container.startswith(kind) and section == label_norm


def _address_matches_filter(address: str, target_filter: str) -> bool:
    if not target_filter:
        return True
    normalized_address = address.casefold().replace(" ", "")
    normalized_filter = target_filter.casefold().replace(" ", "")
    return normalized_filter in normalized_address


def _matches_legal_source(op: LegalOperation, source_filter: str) -> bool:
    source_id = op.source.statute_id if op.source is not None else ""
    source_id = source_id.strip()
    wanted = source_filter.strip()
    return source_id == wanted or source_id.removeprefix("ee/") == wanted


def _legal_op_row(op: LegalOperation) -> dict[str, Any]:
    action = op.action.value if hasattr(op.action, "value") else str(op.action)
    source = op.source.statute_id if op.source is not None else ""
    row: dict[str, Any] = {
        "sequence": op.sequence,
        "action": action,
        "target": str(op.target),
        "source_statute": source,
        "op_id": op.op_id,
    }
    if op.witness_rule_id:
        row["witness_rule_id"] = op.witness_rule_id
    if op.destination is not None:
        row["destination"] = str(op.destination)
    if op.payload is not None and op.payload.text:
        row["payload_preview"] = op.payload.text[:180]
    if op.text_patch is not None:
        row["text_patch"] = {
            "kind": op.text_patch.kind.value,
            "match_text": op.text_patch.selector.match_text,
            "replacement": op.text_patch.replacement,
        }
    return row


def _adjudication_row(adjudication: Any) -> dict[str, Any]:
    return {
        "kind": getattr(adjudication, "kind", ""),
        "message": getattr(adjudication, "message", ""),
        "source_statute": getattr(adjudication, "source_statute", ""),
        "op_id": getattr(adjudication, "op_id", ""),
        "detail": dict(getattr(adjudication, "detail", {}) or {}),
    }


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def _ops_sync(
    sid: str,
    source_filter: Optional[str],
    target_filter: Optional[str],
    mode: Literal["finlex_oracle", "legal_pit"],
) -> None:
    compiled_ops: list = []
    replay_xml(sid, mode=mode, compiled_ops_out=compiled_ops, quiet=True, build_full_products=False)

    # Apply filters
    ops = compiled_ops
    if source_filter:
        ops = [op for op in ops if _matches_source(op, source_filter)]
    if target_filter:
        ops = [op for op in ops if _matches_target(op, target_filter)]

    print(f"Statute  : {sid}")
    print(f"Ops total: {len(compiled_ops)}  shown: {len(ops)}")
    if source_filter:
        print(f"Filter   : source={source_filter}")
    if target_filter:
        print(f"Filter   : target={target_filter}")
    print()

    if not ops:
        print("(no operations match filters)")
        return

    # Group by source amendment for readability
    current_source = None
    for op in ops:
        src = op.get("source_statute", "?")
        action = op.get("action", "?").upper()
        target = op.get("target", {})
        addr = _fmt_target(target)
        title = op.get("source_title", "")[:50]
        seq = op.get("sequence", "?")

        if src != current_source:
            print(f"--- {src}  {title}")
            current_source = src

        print(f"  [{seq:3}] {action:<8}  {addr}")

    print()


def _resolve_ee_as_of(*, oracle_id: str, explicit_as_of: str) -> str:
    if explicit_as_of:
        return explicit_as_of
    if not oracle_id:
        print("ERROR: lawvm ops -j ee requires --oracle-id or --as-of", file=sys.stderr)
        raise SystemExit(2)
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive

    archive = open_rt_archive(readonly=True)
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        return extract_effective_date(oracle_xml) or "9999-12-31"
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()


def _ops_ee_sync(
    base_id: str,
    source_filter: Optional[str],
    target_filter: Optional[str],
    *,
    oracle_id: str,
    as_of: str,
    verbose: bool,
    emit_json: bool,
) -> None:
    from lawvm.estonia.replay import replay_ee_to_pit

    resolved_as_of = _resolve_ee_as_of(oracle_id=oracle_id, explicit_as_of=as_of)
    result = replay_ee_to_pit(
        base_id,
        resolved_as_of,
        oracle_id=oracle_id or None,
        verbose=verbose,
    )
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        raise SystemExit(1)

    ops = list(result.compiled_ops)
    if source_filter:
        ops = [op for op in ops if _matches_legal_source(op, source_filter)]
    if target_filter:
        ops = [op for op in ops if _address_matches_filter(str(op.target), target_filter)]

    rows = [_legal_op_row(op) for op in ops]
    adjudication_rows = [_adjudication_row(adjudication) for adjudication in result.adjudications]
    if emit_json:
        print(json.dumps(
            {
                "jurisdiction": "ee",
                "base_id": base_id,
                "oracle_id": result.oracle_id,
                "as_of": resolved_as_of,
                "title": result.base_title,
                "ops_total": len(result.compiled_ops),
                "ops_shown": len(rows),
                "snapshot_ops": len(result.applied_snapshot_ops),
                "adjudications": adjudication_rows,
                "ops": rows,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    print("Jurisdiction: ee")
    print(f"Base        : {base_id}")
    print(f"Oracle      : {result.oracle_id or oracle_id or '(none)'}")
    print(f"As of       : {resolved_as_of}")
    print(f"Title       : {result.base_title}")
    print(f"Ops total   : {len(result.compiled_ops)}  shown: {len(rows)}")
    print(f"Snapshots   : {len(result.applied_snapshot_ops)}")
    print(f"Adjudications: {len(adjudication_rows)}")
    if source_filter:
        print(f"Filter      : source={source_filter}")
    if target_filter:
        print(f"Filter      : target={target_filter}")
    print()

    if not rows:
        print("(no operations match filters)")
    else:
        current_source = None
        for row in rows:
            src = row.get("source_statute") or "?"
            if src != current_source:
                print(f"--- {src}")
                current_source = src
            rule = f"  [{row['witness_rule_id']}]" if row.get("witness_rule_id") else ""
            print(
                f"  [{row['sequence']:3}] {row['action'].upper():<12}  "
                f"{row['target']}{rule}"
            )
            if row.get("destination"):
                print(f"       -> {row['destination']}")
            if row.get("payload_preview"):
                print(f"       payload: {row['payload_preview']}")
    if adjudication_rows:
        print()
        print("Adjudications:")
        for row in adjudication_rows[:20]:
            print(
                f"  {row['kind']} source={row['source_statute'] or '?'} "
                f"op={row['op_id'] or '?'}: {row['message']}"
            )
        if len(adjudication_rows) > 20:
            print(f"  ... and {len(adjudication_rows) - 20} more")


def _ops_uk_sync(
    sid: str,
    source_filter: Optional[str],
    target_filter: Optional[str],
    emit_json: bool,
) -> None:
    from collections import Counter
    from pathlib import Path

    from farchive import Farchive
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_state import is_uk_affecting_act_xml_source_observation
    from lawvm.core.compile_records import is_blocking_compile_record

    _repo_root = Path(__file__).resolve().parents[3]
    db_path = _repo_root / "data" / "uk_legislation.farchive"
    if not db_path.exists():
        print(f"ERROR: archive not found at {db_path}", file=sys.stderr)
        raise SystemExit(1)

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(db_path) as archive:
        pipeline = uk_replay_module.UKReplayPipeline(_repo_root)
        ops = pipeline.compile_ops_for_statute(
            sid,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

    effect_source_pathology_observations = [
        row
        for row in effect_diagnostics
        if str(row.get("rule_id") or "") == "uk_effect_source_pathology_classified"
    ]
    manual_compile_frontier_observations = [
        row
        for row in effect_diagnostics
        if str(row.get("rule_id") or "") == "uk_manual_compile_frontier_classified"
    ]
    source_acquisition_rejections = [
        row
        for row in effect_diagnostics
        if is_uk_affecting_act_xml_source_observation(row)
    ]

    all_compile_observations: list[dict[str, Any]] = [
        *effect_feed_parse_rejections,
        *effect_source_pathology_observations,
        *manual_compile_frontier_observations,
        *source_acquisition_rejections,
        *lowering_rejections,
        *authority_rejections,
    ]
    blocking_rejections = [
        row for row in all_compile_observations if is_blocking_compile_record(row)
    ]
    rejection_rule_counts: dict[str, int] = dict(
        sorted(Counter(str(row.get("rule_id") or "unknown") for row in blocking_rejections).items())
    )

    # Apply filters
    filtered_ops = list(ops)
    if source_filter:
        filtered_ops = [
            op for op in filtered_ops
            if source_filter.strip() in (op.source.statute_id if op.source is not None else "")
        ]
    if target_filter:
        filtered_ops = [
            op for op in filtered_ops
            if target_filter.casefold() in str(op.target).casefold()
        ]

    if emit_json:
        def _op_to_dict(op: LegalOperation) -> dict[str, Any]:
            action = op.action.value if hasattr(op.action, "value") else str(op.action)
            source_id = op.source.statute_id if op.source is not None else ""
            row: dict[str, Any] = {
                "op_id": op.op_id,
                "sequence": op.sequence,
                "action": action,
                "target": str(op.target),
                "source_statute": source_id,
            }
            if op.witness_rule_id:
                row["witness_rule_id"] = op.witness_rule_id
            if op.payload is not None:
                row["payload_kind"] = str(op.payload.kind)
            if op.destination is not None:
                row["destination"] = str(op.destination)
            if op.source is not None and op.source.effective:
                row["source_effective"] = op.source.effective
            return row

        rejection_rows = [
            {
                "rule_id": str(row.get("rule_id") or ""),
                "blocking": bool(is_blocking_compile_record(row)),
                "affected_provision": str(row.get("affected_provision") or row.get("affected_provisions") or ""),
                "effect_type": str(row.get("effect_type") or ""),
            }
            for row in all_compile_observations
        ]
        print(json.dumps(
            {
                "jurisdiction": "uk",
                "statute_id": sid,
                "ops_count": len(ops),
                "ops_shown": len(filtered_ops),
                "rejection_rule_counts": rejection_rule_counts,
                "ops": [_op_to_dict(op) for op in filtered_ops],
                "rejections": rejection_rows,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    # Human-readable output
    print("Jurisdiction: uk")
    print(f"Statute     : {sid}")
    print(f"Ops total   : {len(ops)}  shown: {len(filtered_ops)}")
    if source_filter:
        print(f"Filter      : source={source_filter}")
    if target_filter:
        print(f"Filter      : target={target_filter}")
    print()

    if not filtered_ops:
        print("(no operations match filters)")
    else:
        current_source = None
        for idx, op in enumerate(filtered_ops):
            src = op.source.statute_id if op.source is not None else "?"
            action = op.action.value if hasattr(op.action, "value") else str(op.action)
            target_str = str(op.target)
            if src != current_source:
                src_title = op.source.title[:60] if op.source is not None and op.source.title else ""
                print(f"--- {src}  {src_title}")
                current_source = src
            rule = f"  [{op.witness_rule_id}]" if op.witness_rule_id else ""
            print(f"  [{idx:3}] {action.upper():<14}  {target_str}{rule}")
            if op.payload is not None:
                print(f"       payload_kind: {op.payload.kind}")
            if op.destination is not None:
                print(f"       -> {op.destination}")

    if all_compile_observations:
        print()
        print(f"Rejections: {len(blocking_rejections)} blocking, "
              f"{len(all_compile_observations)} total observations")
        for rule_id, count in rejection_rule_counts.items():
            print(f"  {rule_id}: {count}")


def main(args) -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi")
    if jurisdiction == "ee":
        _ops_ee_sync(
            base_id=args.statute_id,
            source_filter=getattr(args, "source", None),
            target_filter=getattr(args, "target", None),
            oracle_id=getattr(args, "oracle_id", "") or "",
            as_of=getattr(args, "as_of", "") or "",
            verbose=getattr(args, "verbose", False),
            emit_json=getattr(args, "json", False),
        )
        return
    if jurisdiction == "uk":
        _ops_uk_sync(
            sid=args.statute_id,
            source_filter=getattr(args, "source", None),
            target_filter=getattr(args, "target", None),
            emit_json=getattr(args, "json", False),
        )
        return
    if jurisdiction != "fi":
        print(f"ERROR: lawvm ops does not yet support -j {jurisdiction}", file=sys.stderr)
        raise SystemExit(2)
    _ops_sync(
        sid=args.statute_id,
        source_filter=getattr(args, "source", None),
        target_filter=getattr(args, "target", None),
        mode=getattr(args, "mode", "finlex_oracle"),
    )
