#!/usr/bin/env python3
"""acquire_eu_amenders.py — durably acquire missing/wrong EU amenders + corrigenda (#9).

The EU oracle-touch closure (``lawvm.tools.eu_anchor_manifest``) under-applies
because three classes of amending instrument are absent or wrong in the
``eu_cellar.farchive``:

  1. TRULY-MISSING amenders — never fetched (e.g. 32016R0646 / 32017R1221 →
     32008R0692, 32016R1185 → 32012R0923).
  2. WRONG-MANIFESTATION-ITEM stores — the first acquisition run stored a ``DOC``
     publication envelope, an ``ANNEX`` member, or even a binary TIFF attachment
     in lieu of the ``ACT`` body (the notice lists them as sibling ``…/DOC_N``
     items of one manifestation; first-with-url selection landed on the wrong
     one). :func:`lawvm.eu.eu_acquire.acquire_amender_act` re-fetches through the
     sibling-``DOC_N`` ACT-body resolver.
  3. CORRIGENDA byte-acquisition — the corrigendum ``…R(NN)`` CELEX is not
     resolvable via ``/celex/`` (404); this walks each frozen base's tree notice,
     extracts the ``CORRECTED_BY`` corrigendum resources (celex + Cellar UUID),
     and acquires each ``CORR`` body via
     :func:`lawvm.eu.eu_acquire.acquire_corrigendum`.

Durable acquisition into the farchive under the identity locator
``cellar://celex/{CELEX}/enacted/eng/fmx4`` (the same locator the anchor closure
reads via ``_fetch_fmx4_bytes``). Idempotent + resumable: an already-correct
ACT-rooted store re-observes (does not re-store).

Usage:
    uv run python scripts/acquire_eu_amenders.py                  # all classes
    uv run python scripts/acquire_eu_amenders.py --amenders-only
    uv run python scripts/acquire_eu_amenders.py --corrigenda-only
    uv run python scripts/acquire_eu_amenders.py --celexes 32016R0646,32017R1221
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: XML roots that ARE a self-contained act / corrigendum body (mirror of the
#: private set in :mod:`lawvm.eu.eu_acquire` — used here only to CENSUS what is
#: already stored so a correct store is skipped).
_ACT_BODY_ROOTS = ("ACT", "CORR", "CONS.ACT", "CONS.DOC")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_farchive() -> tuple[Any, str]:
    from farchive import Farchive

    from lawvm.corpus_store import resolve_farchive_path

    dest_path, _rule = resolve_farchive_path("eu_cellar.farchive")
    return Farchive(str(dest_path)), str(dest_path)


def _stored_root(farchive: Any, celex: str, language: str = "eng") -> str:
    """Root local-tag of the currently-stored enacted FMX4 for ``celex``, or ''."""
    from lawvm.eu.eu_acquire import celex_locator

    for lang in (language, "fin"):
        data = farchive.get(celex_locator(celex, "enacted", lang, "fmx4"))
        if data:
            try:
                tag = ET.fromstring(data).tag
                return tag.rsplit("}", 1)[-1] if "}" in tag else tag
            except ET.ParseError:
                return "NOT-XML"
    return ""


def _closure_amender_celexes() -> list[str]:
    """Every unique ``amends``-edge CELEX across the frozen oracle-base closure."""
    from lawvm.tools.eu_anchor_manifest import (
        REAL_ANCHOR_EU_AMENDMENT_CLOSURE,
        REAL_ANCHOR_EU_ORACLE_BASES,
    )

    out: list[str] = []
    seen: set[str] = set()
    for base in REAL_ANCHOR_EU_ORACLE_BASES:
        for edge in REAL_ANCHOR_EU_AMENDMENT_CLOSURE.get(base, ()):
            if edge.relation_kind != "amends":
                continue
            if "(" in edge.celex:  # corrigendum-labelled edge, not an act CELEX
                continue
            if edge.celex not in seen:
                seen.add(edge.celex)
                out.append(edge.celex)
    return out


def acquire_amenders(
    farchive: Any,
    *,
    celexes: list[str],
    timeout_s: int,
    force: bool,
) -> list[dict[str, Any]]:
    """Re-acquire each amender's ACT body where absent or wrong-manifestation."""
    from lawvm.eu.eu_acquire import acquire_amender_act

    reports: list[dict[str, Any]] = []
    for i, celex in enumerate(celexes, 1):
        before = _stored_root(farchive, celex)
        if before in _ACT_BODY_ROOTS and not force:
            print(f"[amender {i}/{len(celexes)}] {celex} already ACT-rooted ({before}) — skip", flush=True)
            reports.append({"celex": celex, "acquire_status": "SKIP_ALREADY_ACT", "root": before})
            continue
        print(f"[amender {i}/{len(celexes)}] {celex} (stored root={before or 'ABSENT'}) ...", flush=True)
        rep = acquire_amender_act(farchive, celex, fetched_at=_now(), timeout_s=timeout_s)
        rep["prior_root"] = before
        reports.append(rep)
        print(f"    => {rep['status']} root={rep.get('root','')}", flush=True)
    return reports


def acquire_corrigenda(
    farchive: Any,
    *,
    bases: list[str],
    timeout_s: int,
    force: bool,
) -> list[dict[str, Any]]:
    """Acquire the ``CORR`` body of every corrigendum named by each base notice."""
    from lawvm.eu.eu_acquire import (
        _live_fetch_notice,
        acquire_corrigendum,
        extract_corrigendum_resources,
    )

    reports: list[dict[str, Any]] = []
    for base in bases:
        try:
            notice_bytes, _ = _live_fetch_notice(base, "eng", timeout_s)
        except Exception as exc:  # noqa: BLE001 — record notice gap, continue
            print(f"[corrig base {base}] NOTICE_FAIL {type(exc).__name__}", flush=True)
            reports.append({"base": base, "acquire_status": f"BASE_NOTICE_FAIL:{type(exc).__name__}"})
            continue
        resources, looked = extract_corrigendum_resources(notice_bytes)
        print(f"[corrig base {base}] looked={looked} corrigenda={len(resources)}", flush=True)
        for res in resources:
            before = _stored_root(farchive, res.celex)
            if before in _ACT_BODY_ROOTS and not force:
                print(f"    {res.celex} already {before} — skip", flush=True)
                reports.append({"base": base, "celex": res.celex, "acquire_status": "SKIP_ALREADY", "root": before})
                continue
            rep = acquire_corrigendum(farchive, res, fetched_at=_now(), timeout_s=timeout_s)
            rep["base"] = base
            rep["prior_root"] = before
            reports.append(rep)
            print(f"    {res.celex} => {rep['status']} root={rep.get('root','')}", flush=True)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--celexes", default=None, help="comma-separated amender CELEXes (default: whole frozen closure)")
    parser.add_argument("--bases", default=None, help="comma-separated corrigendum base CELEXes (default: frozen oracle bases)")
    parser.add_argument("--amenders-only", action="store_true")
    parser.add_argument("--corrigenda-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-fetch even already-ACT-rooted stores")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--out", type=Path, default=_REPO_ROOT / ".tmp" / "eu_amender_acquisition.json")
    args = parser.parse_args(argv)

    from lawvm.tools.eu_anchor_manifest import REAL_ANCHOR_EU_ORACLE_BASES

    amender_celexes = (
        [c.strip() for c in args.celexes.split(",") if c.strip()]
        if args.celexes
        else _closure_amender_celexes()
    )
    corrig_bases = (
        [b.strip() for b in args.bases.split(",") if b.strip()]
        if args.bases
        else list(REAL_ANCHOR_EU_ORACLE_BASES)
    )

    farchive, path = _open_farchive()
    print(f"farchive: {path}", flush=True)
    amender_reports: list[dict[str, Any]] = []
    corrig_reports: list[dict[str, Any]] = []
    try:
        if not args.corrigenda_only:
            amender_reports = acquire_amenders(
                farchive, celexes=amender_celexes, timeout_s=args.timeout_s, force=args.force
            )
        if not args.amenders_only:
            corrig_reports = acquire_corrigenda(
                farchive, bases=corrig_bases, timeout_s=args.timeout_s, force=args.force
            )
    finally:
        close = getattr(farchive, "close", None)
        if callable(close):
            close()

    def _count(reps: list[dict[str, Any]], status_prefix: str) -> int:
        return sum(1 for r in reps if str(r.get("acquire_status", "")).startswith(status_prefix))

    summary = {
        "farchive": path,
        "amenders_stored": _count(amender_reports, "STORED"),
        "amenders_reobserved": _count(amender_reports, "RE_OBSERVED"),
        "amenders_skipped": _count(amender_reports, "SKIP"),
        "amenders_failed": sum(
            1 for r in amender_reports
            if not str(r.get("acquire_status", "")).startswith(("STORED", "RE_OBSERVED", "SKIP"))
        ),
        "corrigenda_stored": _count(corrig_reports, "STORED"),
        "corrigenda_reobserved": _count(corrig_reports, "RE_OBSERVED"),
        "corrigenda_skipped": _count(corrig_reports, "SKIP"),
        "corrigenda_failed": sum(
            1 for r in corrig_reports
            if not str(r.get("acquire_status", "")).startswith(("STORED", "RE_OBSERVED", "SKIP"))
        ),
        "amender_reports": amender_reports,
        "corrigendum_reports": corrig_reports,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(
        f"\nDONE amenders: stored={summary['amenders_stored']} "
        f"reobs={summary['amenders_reobserved']} skip={summary['amenders_skipped']} "
        f"fail={summary['amenders_failed']}",
        flush=True,
    )
    print(
        f"DONE corrigenda: stored={summary['corrigenda_stored']} "
        f"reobs={summary['corrigenda_reobserved']} skip={summary['corrigenda_skipped']} "
        f"fail={summary['corrigenda_failed']} -> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
