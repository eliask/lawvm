#!/usr/bin/env python3
"""W6 feasibility probe — typed-accessor vs stored ``target_*`` column parity.

The W6–W9 wave routes the ~2.5k ``op.target_*`` column READ sites through the
typed accessor (``AmendmentOp.target_selector``) and then deletes the 8 legacy
``target_*`` columns + the bridging adapters. The go/no-go question this probe
answers, over the WHOLE pinned corpus and EVERY compiled ``AmendmentOp``:

    For every op, does the value obtained via the typed accessor reproduce the
    stored ``target_*`` column EXACTLY, for every one of the 8 columns?

Concretely: ``TargetSelectorCodecV1.to_legacy(op.target_selector)`` is the
typed-accessor view re-projected back to the legacy 8-tuple. We compare it,
COLUMN BY COLUMN, against the op's live stored columns. Any per-column
disagreement is a mismatch and is CHARACTERIZED by class (the tricky cases the
read-migration must handle): codec-raise, ``lo``-absent ops, empty/degenerate
focus label, unknown unit kind, etc.

Harvest point: ``process_pipeline.compile_amendment_ops`` — the single
production chokepoint every replayed amendment's ``ops`` list flows through (see
``tests/test_target_selector_consistency.py``). This captures ops from ALL
construction sites (``AmendmentOp.from_lo`` and the direct ``AmendmentOp(...)``
builders in frontend/johtolause/recovery), i.e. the true production population.

Corpus: the full ``tests.corpus_pin_helpers.ORACLE_VERSIONS`` pin set (~210
statutes). Requires the populated ``data/finlex.farchive``; in a worktree set
``LAWVM_CANONICAL_DATA_ROOT`` to a checkout whose ``data/`` holds it.

Writes ``.tmp/w6_target_column_accessor_parity.json``. Deterministic: same pins,
same ops, same verdict. Replay is read-only; nothing is mutated.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The 8 legacy columns under migration (target_special_raw is NOT a stored
# AmendmentOp column — special_raw lives on the TargetSelector; the stored legacy
# column is target_special. The codec preserves the raw token via special_raw).
_COLUMNS: tuple[str, ...] = (
    "target_unit_kind",
    "target_section",
    "target_chapter",
    "target_part",
    "target_paragraph",
    "target_item",
    "target_subitem",
    "target_special",
)

_DEFAULT_OUT = _REPO_ROOT / ".tmp" / "w6_target_column_accessor_parity.json"


def _legacy_tuple_of(op: Any) -> dict[str, Any]:
    """The op's live stored 8 legacy ``target_*`` columns as a plain dict."""
    return {col: getattr(op, col) for col in _COLUMNS}


def _accessor_tuple_of(op: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The typed-accessor view re-projected to the legacy 8-tuple.

    Returns ``(columns_dict, None)`` on success, or ``(None, error_repr)`` if the
    typed accessor or its re-encode raised (a codec FINDING / blocker shape).
    """
    from lawvm.finland.target_selector_codec import TargetSelectorCodecV1

    try:
        selector = op.target_selector
        rec = TargetSelectorCodecV1.to_legacy(selector)
    except Exception as exc:  # noqa: BLE001 — surface the offending shape
        return None, f"{type(exc).__name__}: {exc}"
    return asdict(rec), None


def _classify_mismatch(legacy: dict[str, Any], op: Any) -> str:
    """Characterize a mismatching op into a handling class for the migration."""
    if op.lo is None:
        return "lo_absent__columns_are_sole_source"
    if not legacy["target_section"]:
        return "empty_focus_label"
    if legacy["target_unit_kind"] not in ("section", "chapter", "part"):
        return "unknown_unit_kind"
    return "other_value_drift"


def _harvest_ops(statute_ids: list[str], pins: dict[str, str]) -> tuple[list[Any], dict[str, str]]:
    """Replay each pinned statute, capturing every op via compile_amendment_ops."""
    import lawvm.finland.process_pipeline as process_pipeline
    from tests.corpus_pin_helpers import pinned_replay

    captured: list[Any] = []
    real_compile = process_pipeline.compile_amendment_ops

    def _capturing_compile(state: Any, ops: list[Any], *args: Any, **kwargs: Any) -> Any:
        captured.extend(ops)
        return real_compile(state, ops, *args, **kwargs)

    process_pipeline.compile_amendment_ops = _capturing_compile
    replay_errors: dict[str, str] = {}
    try:
        for sid in statute_ids:
            if sid not in pins:
                continue
            try:
                pinned_replay(
                    sid,
                    mode="official_consolidation",
                    quiet=True,
                    build_full_products=False,
                )
            except Exception as exc:  # noqa: BLE001 — record, continue corpus
                replay_errors[sid] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        process_pipeline.compile_amendment_ops = real_compile
    return captured, replay_errors


def run_probe(statute_ids: list[str] | None = None) -> dict[str, Any]:
    """Run the full-corpus parity probe and return the JSON-serializable report."""
    from tests.corpus_pin_helpers import ORACLE_VERSIONS

    ids = sorted(statute_ids) if statute_ids is not None else sorted(ORACLE_VERSIONS)
    ops, replay_errors = _harvest_ops(ids, ORACLE_VERSIONS)

    per_column_mismatch: Counter[str] = Counter()
    mismatch_classes: Counter[str] = Counter()
    accessor_raises: Counter[str] = Counter()
    ops_with_any_mismatch = 0
    examples: dict[str, list[dict[str, Any]]] = {}

    for op in ops:
        legacy = _legacy_tuple_of(op)
        accessor, err = _accessor_tuple_of(op)
        if err is not None:
            accessor_raises[err.split(":")[0]] += 1
            mismatch_classes["accessor_raised"] += 1
            ops_with_any_mismatch += 1
            examples.setdefault("accessor_raised", [])
            if len(examples["accessor_raised"]) < 5:
                examples["accessor_raised"].append(
                    {"legacy": legacy, "error": err, "lo_present": op.lo is not None}
                )
            continue
        assert accessor is not None  # err is None ⇒ accessor populated
        op_mismatched_cols = [c for c in _COLUMNS if accessor[c] != legacy[c]]
        if not op_mismatched_cols:
            continue
        ops_with_any_mismatch += 1
        for c in op_mismatched_cols:
            per_column_mismatch[c] += 1
        cls = _classify_mismatch(legacy, op)
        mismatch_classes[cls] += 1
        examples.setdefault(cls, [])
        if len(examples[cls]) < 5:
            examples[cls].append(
                {
                    "legacy": legacy,
                    "accessor": accessor,
                    "mismatched_columns": op_mismatched_cols,
                    "lo_present": op.lo is not None,
                }
            )

    verdict = (
        "GO__mechanical_low_risk"
        if ops_with_any_mismatch == 0
        else "BLOCKERS_FOUND__characterized_below"
    )

    return {
        "probe": "w6_target_column_accessor_parity",
        "harvest_point": "process_pipeline.compile_amendment_ops",
        "columns_checked": list(_COLUMNS),
        "statutes_pinned": len(ids),
        "statutes_replayed": len(ids) - len(replay_errors),
        "replay_errors": replay_errors,
        "total_ops": len(ops),
        "ops_with_any_mismatch": ops_with_any_mismatch,
        "per_column_mismatch_counts": {c: per_column_mismatch.get(c, 0) for c in _COLUMNS},
        "mismatch_classes": dict(mismatch_classes),
        "accessor_raise_types": dict(accessor_raises),
        "examples_by_class": examples,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="output JSON path (default: .tmp/w6_target_column_accessor_parity.json)",
    )
    parser.add_argument(
        "--statutes",
        default=None,
        help="comma-separated statute ids to restrict to (default: all pins)",
    )
    args = parser.parse_args(argv)

    ids = args.statutes.split(",") if args.statutes else None
    try:
        report = run_probe(ids)
    except Exception:  # noqa: BLE001 — surface a probe-level failure loudly
        traceback.print_exc()
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"total_ops              : {report['total_ops']}")
    print(f"statutes_replayed      : {report['statutes_replayed']}/{report['statutes_pinned']}")
    print(f"ops_with_any_mismatch  : {report['ops_with_any_mismatch']}")
    print(f"per_column_mismatch    : {report['per_column_mismatch_counts']}")
    print(f"mismatch_classes       : {report['mismatch_classes']}")
    print(f"VERDICT                : {report['verdict']}")
    print(f"wrote                  : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
