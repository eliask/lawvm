"""CLI for the oracle-defect external-confirmation rail.

Run as ``python -m lawvm.tools.oracle_defect_confirmation_cli <cmd>`` (or via
``uv run``).  Subcommands:

* ``record``   — append a new keeper confirmation to the store.
* ``list``     — list stored confirmations (text or ``--json``).
* ``coverage`` — report the externally-validated oracle-defect count against a
  residual-id inventory supplied on the command line or via a file.

This is read-only telemetry.  ``record`` only writes the confirmation store; no
subcommand touches replay, scoring, or gating.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from lawvm.tools.oracle_defect_confirmation import (
    OracleDefectExternalConfirmation,
    add_confirmation,
    compute_coverage,
    load_confirmations,
)


def _store_path_arg(args: argparse.Namespace) -> Path | None:
    store = getattr(args, "store", None)
    return Path(store) if store else None


def _read_inventory(args: argparse.Namespace) -> tuple[str, ...]:
    ids: list[str] = list(getattr(args, "residual_id", None) or [])
    inv_file = getattr(args, "inventory_file", None)
    if inv_file:
        text = Path(inv_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                ids.append(stripped)
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for rid in ids:
        if rid and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return tuple(out)


def _cmd_record(args: argparse.Namespace) -> int:
    record = OracleDefectExternalConfirmation(
        confirmation_id=args.id,
        source=args.source,
        ticket=args.ticket,
        submitted_date=args.submitted_date,
        keeper_response=args.keeper_response,
        affected_residual_ids=tuple(args.residual_id or ()),
        correction_date=args.correction_date or "",
        note=args.note or "",
    )
    path = add_confirmation(record, _store_path_arg(args))
    print(f"recorded confirmation {record.confirmation_id!r} -> {path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    confirmations = load_confirmations(_store_path_arg(args))
    if args.json:
        print(
            json.dumps(
                [rec.to_dict() for rec in confirmations],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not confirmations:
        print("(no confirmations recorded)")
        return 0
    for rec in confirmations:
        flag = "confirming" if rec.is_confirming else rec.keeper_response
        corr = f" corrected={rec.correction_date}" if rec.correction_date else ""
        print(
            f"{rec.confirmation_id}  [{rec.source}]  {rec.keeper_response}  "
            f"({flag}){corr}  ticket={rec.ticket}  "
            f"residuals={len(rec.affected_residual_ids)}  submitted={rec.submitted_date}"
        )
        for rid in rec.affected_residual_ids:
            print(f"    - {rid}")
        if rec.note:
            print(f"    note: {rec.note}")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    confirmations = load_confirmations(_store_path_arg(args))
    inventory: Iterable[str] = _read_inventory(args)
    coverage = compute_coverage(inventory, confirmations)
    if args.json:
        print(json.dumps(coverage.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print("=== oracle-defect external-confirmation coverage ===")
    print(f"  inventory residual ids        : {coverage.inventory_residual_count}")
    print(f"  externally-validated (ack/corr): {coverage.externally_validated_count}")
    print(f"  pending (referenced, no ack)   : {len(coverage.pending_residual_ids)}")
    print(f"  dangling (ref, not in inventory): {len(coverage.dangling_residual_ids)}")
    print(f"  confirmations total            : {coverage.confirmations_total}")
    print(f"  confirmations confirming       : {coverage.confirmations_confirming}")
    if coverage.confirmed_residual_ids:
        print("  externally-validated residual ids:")
        for rid in coverage.confirmed_residual_ids:
            print(f"    - {rid}")
    if coverage.dangling_residual_ids:
        print("  dangling residual ids (stale references):")
        for rid in coverage.dangling_residual_ids:
            print(f"    ! {rid}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lawvm-oracle-defect-confirmation",
        description=(
            "Record and report third-party (keeper) confirmations that an "
            "oracle_suspect divergence was oracle-side. Read-only telemetry."
        ),
    )
    parser.add_argument(
        "--store",
        default=None,
        help="path to the confirmation store JSON (default: data/oracle_defect_confirmations.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a new keeper confirmation")
    rec.add_argument("--id", required=True, help="stable confirmation_id")
    rec.add_argument("--source", required=True, help="keeper key, e.g. finlex")
    rec.add_argument("--ticket", required=True, help="keeper reference/id")
    rec.add_argument("--submitted-date", dest="submitted_date", required=True, help="YYYY-MM-DD")
    rec.add_argument(
        "--keeper-response",
        dest="keeper_response",
        required=True,
        choices=("pending", "acknowledged", "corrected", "rejected"),
    )
    rec.add_argument(
        "--residual-id",
        dest="residual_id",
        action="append",
        required=True,
        help="an affected residual_id (repeatable)",
    )
    rec.add_argument("--correction-date", dest="correction_date", default="", help="YYYY-MM-DD (corrected only)")
    rec.add_argument("--note", default="", help="free-text note")
    rec.set_defaults(func=_cmd_record)

    lst = sub.add_parser("list", help="list stored confirmations")
    lst.add_argument("--json", action="store_true", help="emit JSON")
    lst.set_defaults(func=_cmd_list)

    cov = sub.add_parser("coverage", help="report externally-validated oracle-defect coverage")
    cov.add_argument(
        "--residual-id",
        dest="residual_id",
        action="append",
        default=[],
        help="an inventory residual_id (repeatable)",
    )
    cov.add_argument(
        "--inventory-file",
        dest="inventory_file",
        default=None,
        help="file with one inventory residual_id per line (# comments allowed)",
    )
    cov.add_argument("--json", action="store_true", help="emit JSON")
    cov.set_defaults(func=_cmd_coverage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
