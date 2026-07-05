#!/usr/bin/env python3
"""acquire_eu_consolidations.py — durably store sector-0 EU consolidations (#221).

The EU CTSF gate currently scores the WEAK conserved-apply invariant
(``|applied| + |skipped| == |ops|``) because the ``eu_cellar.farchive`` stores
NO sector-0 consolidation — every act is stored at ``enacted`` (see
``lawvm.tools.eu_anchor_manifest`` header). This script closes that gap: for each
base CELEX it enumerates the published dated sector-0 consolidations from the
live Cellar SPARQL endpoint, fetches each consolidated FMX4 body, and stores it
into the farchive under the canonical locator

    cellar://celex/{base_celex}/{YYYYMMDD}/eng/fmx4

so the anchor-touch scorer can read a REAL published consolidation offline and
diff it against ``replay(base + amenders)@date`` — the same oracle-touch surface
FI/EE/UK/NZ use. This is the ``authoritative oracle`` acquisition ONLY; the
comparator (``eu_oracle_divergence``) never repairs the replay toward it.

Idempotent + resumable: an already-stored (base, date) is skipped via
``_store_if_new`` digest comparison. Per-item network failures are logged and
skipped (the series continues); a base with zero published consolidations is
recorded and skipped (its conserved-apply lane remains the correct fallback).

MUST be run under the PERSISTENT session as a durable background job, never a
transient ``systemd --scope`` tied to an agent (the earlier EU ZIP sweep died
at 233/1549 that way). Resumable: re-run with the same args.

Usage:
    uv run python scripts/acquire_eu_consolidations.py            # frozen bases
    uv run python scripts/acquire_eu_consolidations.py --bases 32008R0692,32019R0787
    uv run python scripts/acquire_eu_consolidations.py --limit-per-base 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The unique BASE CELEXes of the frozen EU corpus chains (ctsf_gate
# REAL_ANCHOR_EU_CORPUS_CHAINS). These are the acts the conserved-apply lane
# already replays; acquiring their published consolidations upgrades them from
# the weak invariant to a real oracle-touch score.
_FROZEN_CORPUS_BASES: tuple[str, ...] = (
    "32008R0402",
    "32008R0692",
    "32009R0754",
    "32009R1284",
    "32010R1093",
    "32012R0923",
    "32017R1576",
    "32019R0787",
    "32022R2309",
)

_STORAGE_CLASS = "eu_consolidation_fmx4"
_LANG = "eng"
_FMT = "fmx4"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_farchive() -> tuple[Any, str]:
    """Open the EU cellar farchive read-write at the resolved canonical path."""
    from farchive import Farchive

    from lawvm.corpus_store import resolve_farchive_path

    dest_path, _rule = resolve_farchive_path("eu_cellar.farchive")
    return Farchive(str(dest_path)), str(dest_path)


def acquire_base(
    farchive: Any,
    base_celex: str,
    *,
    limit_per_base: int | None,
    timeout_s: int,
) -> dict[str, Any]:
    """Enumerate + fetch + store every published consolidation of one base."""
    from lawvm.eu.eu_acquire import _store_if_new, celex_locator
    from lawvm.eu.eu_consolidation_oracle import (
        enumerate_consolidation_series,
        fetch_consolidation_bytes,
        parse_consolidation_date,
    )

    result: dict[str, Any] = {
        "base": base_celex,
        "series": 0,
        "stored": 0,
        "already": 0,
        "failed": [],
    }
    try:
        series = enumerate_consolidation_series(base_celex, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — record enum gap, continue corpus
        result["failed"].append(f"ENUM:{type(exc).__name__}:{str(exc)[:120]}")
        return result

    if limit_per_base is not None:
        series = series[:limit_per_base]
    result["series"] = len(series)

    for consolidated in series:
        try:
            as_of = parse_consolidation_date(consolidated)
        except Exception as exc:  # noqa: BLE001
            result["failed"].append(f"{consolidated}:DATE:{str(exc)[:80]}")
            continue
        locator = celex_locator(base_celex, as_of, _LANG, _FMT)
        # Resumable: skip fetch entirely if this locator already has history.
        if farchive.history(locator):
            result["already"] += 1
            continue
        try:
            raw = fetch_consolidation_bytes(base_celex, as_of, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 — typed byte-lane gap, continue
            result["failed"].append(
                f"{consolidated}:FETCH:{type(exc).__name__}:{str(exc)[:120]}"
            )
            continue
        if not raw:
            result["failed"].append(f"{consolidated}:EMPTY")
            continue
        stored = _store_if_new(
            farchive,
            locator,
            raw,
            storage_class=_STORAGE_CLASS,
            metadata={
                "base_celex": base_celex,
                "consolidated_celex": consolidated,
                "consolidation_date": as_of,
                "language": _LANG,
                "fmt": _FMT,
                "source": "eur-lex-cellar-consolidation",
            },
            observed_at=_now(),
        )
        if stored:
            result["stored"] += 1
        else:
            result["already"] += 1
        print(
            f"  [{base_celex}] {consolidated} -> {len(raw)}B "
            f"{'STORED' if stored else 're-observed'}",
            flush=True,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bases",
        default=None,
        help="comma-separated base CELEXes (default: frozen corpus bases)",
    )
    parser.add_argument(
        "--limit-per-base",
        type=int,
        default=None,
        help="cap consolidations acquired per base (default: all)",
    )
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / ".tmp" / "eu_consolidation_acquisition.json",
    )
    args = parser.parse_args(argv)

    bases = (
        tuple(b.strip() for b in args.bases.split(",") if b.strip())
        if args.bases
        else _FROZEN_CORPUS_BASES
    )

    farchive, path = _open_farchive()
    print(f"farchive: {path}", flush=True)
    print(f"bases: {len(bases)}", flush=True)

    reports: list[dict[str, Any]] = []
    try:
        for i, base in enumerate(bases, 1):
            print(f"[{i}/{len(bases)}] {base} ...", flush=True)
            rep = acquire_base(
                farchive,
                base,
                limit_per_base=args.limit_per_base,
                timeout_s=args.timeout_s,
            )
            reports.append(rep)
            print(
                f"  => series={rep['series']} stored={rep['stored']} "
                f"already={rep['already']} failed={len(rep['failed'])}",
                flush=True,
            )
    finally:
        close = getattr(farchive, "close", None)
        if callable(close):
            close()

    total_stored = sum(r["stored"] for r in reports)
    total_already = sum(r["already"] for r in reports)
    total_failed = sum(len(r["failed"]) for r in reports)
    summary = {
        "farchive": path,
        "bases": list(bases),
        "total_stored": total_stored,
        "total_already": total_already,
        "total_failed": total_failed,
        "per_base": reports,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(
        f"\nDONE: stored={total_stored} already={total_already} "
        f"failed={total_failed} -> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
