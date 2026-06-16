"""lawvm fi-parse-explain — one-clause johtolause parse diagnostic dump.

Given a statute id, fetch its enacting clause (johtolause) from the corpus and
dump everything needed to diagnose how that ONE clause parses, WITHOUT
reverse-engineering the pipeline by hand. This composes existing public APIs;
it re-implements no parsing.

What it dumps:
  1. The raw johtolause text (whitespace-normalized).
  2. parser_lane + grammar_decline_reason (when the grammar declined to legacy).
  3. The OLD-vs-NEW surface-model comparison (equal, or the list of deltas).
  4. The totality predicate result: n_ops + any flagged-drop labels with text.
  5. (--ops) the parsed op codes.

Usage:
    lawvm fi-parse-explain 2002/375
    lawvm fi-parse-explain 2002/375 --ops
    lawvm fi-parse-explain 2002/375 --json
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

_WS_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _load_johtolause(sid: str) -> str | None:
    """Fetch the enacting clause text for ``sid`` from the corpus store.

    Mirrors the exact source-fetch idiom used by the census/parse-bench
    harnesses (``read_source`` falling back to ``read_amendment``).
    """
    from farchive import Farchive

    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path()))
    xb = store.read_source(sid) or store.read_amendment(sid)
    if not xb:
        return None
    try:
        return get_johtolause(xb) or ""
    except Exception:
        return ""


def _ops_payload(parsed_ops: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for op in parsed_ops:
        out.append(
            {
                "verb": op.verb,
                "kind": op.kind,
                "part": op.part,
                "chapter": op.chapter,
                "number": op.number,
                "momentti": op.momentti,
                "item": op.item,
                "facet": str(op.facet) if op.facet is not None else None,
                "renumber_dest": op.renumber_dest,
                "raw": op.raw,
            }
        )
    return out


def _collect(sid: str, *, want_ops: bool) -> dict[str, Any]:
    """Compose the diagnostic record for ``sid`` from the public APIs.

    Returns a JSON-serializable dict. On a missing source / empty clause the
    record carries an ``error`` key and the rest is left absent.
    """
    from lawvm.finland.johtolause import surface_parse
    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.johtolause.grammar import parser as new_parser
    from lawvm.finland.johtolause.grammar.diff import (
        compare_surface_models,
        parse_text_with,
    )
    from lawvm.finland.johtolause.totality import predicate

    raw = _load_johtolause(sid)
    if raw is None:
        return {"statute_id": sid, "error": "no source for statute id"}
    johto = _normalize_ws(raw)
    if not johto:
        return {"statute_id": sid, "error": "no johtolause (non-amendment or empty)"}

    record: dict[str, Any] = {"statute_id": sid, "johtolause": johto}

    # (2) lane + decline reason — the canonical pipeline's own report.
    result = parse_clause(johto, statute_id=sid)
    record["parser_lane"] = result.parser_lane
    record["used_legacy_fallback"] = result.used_legacy_fallback
    record["grammar_decline_reason"] = result.grammar_decline_reason
    record["parse_error"] = result.parse_error

    # (3) OLD-vs-NEW surface-model comparison on identical footing.
    diff: dict[str, Any] = {}
    try:
        old_model = parse_text_with(johto, surface_parse.parse)
    except Exception as exc:  # noqa: BLE001
        diff = {"old_parse_error": f"{type(exc).__name__}: {exc}"}
    else:
        try:
            new_model = parse_text_with(johto, new_parser.parse)
        except new_parser.OutOfScope as exc:
            diff = {"new_declined": str(exc)}
        except Exception as exc:  # noqa: BLE001
            diff = {"new_parse_error": f"{type(exc).__name__}: {exc}"}
        else:
            report = compare_surface_models(old_model, new_model)
            diff = {"equal": report.equal, "deltas": list(report.deltas)}
    record["old_vs_new"] = diff

    # (4) totality predicate — n_ops + flagged silent-drop candidates.
    flagged, n_ops = predicate(johto)
    record["totality"] = {
        "n_ops": n_ops,
        "flagged_drops": [
            {
                "label": fd.label.label,
                "struct_cat": fd.label.struct_cat,
                "reason": fd.reason,
                "source_text": fd.source_text,
            }
            for fd in flagged
        ],
    }

    # (5) optional parsed op codes.
    if want_ops:
        record["parsed_ops"] = _ops_payload(result.parsed_ops)

    return record


def _print_human(record: dict[str, Any], *, want_ops: bool) -> None:
    sid = record["statute_id"]
    if "error" in record:
        print(f"statute {sid}: {record['error']}")
        return

    print("=" * 72)
    print(f"fi-parse-explain  {sid}")
    print("=" * 72)
    print()
    print("johtolause (normalized):")
    print(f"  {record['johtolause']}")
    print()

    print(f"parser_lane            : {record['parser_lane']}")
    print(f"used_legacy_fallback   : {record['used_legacy_fallback']}")
    if record.get("grammar_decline_reason"):
        print(f"grammar_decline_reason : {record['grammar_decline_reason']}")
    if record.get("parse_error"):
        print(f"parse_error            : {record['parse_error']}")
    print()

    diff = record["old_vs_new"]
    print("OLD vs NEW surface model:")
    if "old_parse_error" in diff:
        print(f"  OLD parser crashed: {diff['old_parse_error']}")
    elif "new_declined" in diff:
        print(f"  NEW declined (OutOfScope): {diff['new_declined']}")
    elif "new_parse_error" in diff:
        print(f"  NEW parser crashed: {diff['new_parse_error']}")
    elif diff.get("equal"):
        print("  equal (byte-identical canonical model)")
    else:
        print(f"  {len(diff['deltas'])} delta(s):")
        for d in diff["deltas"]:
            print(f"    {d}")
    print()

    tot = record["totality"]
    print(f"totality predicate: n_ops={tot['n_ops']}")
    if tot["flagged_drops"]:
        print(f"  {len(tot['flagged_drops'])} flagged drop(s):")
        for fd in tot["flagged_drops"]:
            print(
                f"    [{fd['struct_cat']}] {fd['label']!r}  "
                f"({fd['reason']})  src={fd['source_text']!r}"
            )
    else:
        print("  no flagged drops")
    print()

    if want_ops:
        ops = record.get("parsed_ops", [])
        print(f"parsed ops ({len(ops)}):")
        for i, op in enumerate(ops, 1):
            facet = f" facet={op['facet']}" if op["facet"] else ""
            dest = f" renumber_dest={op['renumber_dest']!r}" if op["renumber_dest"] else ""
            print(
                f"  [{i}] verb={op['verb']} kind={op['kind']} "
                f"number={op['number']!r} momentti={op['momentti']} "
                f"item={op['item']!r} chapter={op['chapter']!r} "
                f"part={op['part']!r}{facet}{dest}"
            )
        if not ops:
            print("  (none)")


def main(args: argparse.Namespace) -> None:
    want_ops: bool = bool(getattr(args, "ops", False))
    record = _collect(args.sid, want_ops=want_ops)
    if getattr(args, "json", False):
        print(json.dumps(record, indent=2, default=str))
        return
    _print_human(record, want_ops=want_ops)
