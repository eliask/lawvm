"""``lawvm fi-parse-attachments`` — parse finlex statute PDF attachments to LawVM IR.

Iterates the PDF attachment locators in the (immutable) ``finlex.farchive`` source
store — corrigenda + media — loads each as a ``SourceManifestation``, parses it to
canonical LawVM ``IRNode`` via the deterministic native-PDF pipeline, and persists
the derived IR (content-addressed by source digest × pipeline version) in the
SEPARATE ``fi_parsed_ir.farchive`` derived store. Source stays immutable; the
mutable parse products live elsewhere, with their evidence tiers preserved
(see ``lawvm.finland.source_document.parsed_store``).

A PDF that fails to parse is a TYPED failure record, never a crash and never a
silently dropped attachment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterator, List, Tuple

from lawvm.finland.source_document.parsed_store import (
    PARSED_STORE_DEFAULT,
    ParsedIrStore,
    parse_and_cache,
    resolve_pipeline,
)

_FINLEX_DEFAULT = "data/finlex.farchive"


def _classify(locator: str) -> str:
    """Attachment kind + source_role from a finlex PDF locator."""
    if "corrigenda" in locator:
        return "corrigendum"
    if "/media/" in locator:
        return "attachment"
    return "attachment"


def iter_finlex_pdf_locators(
    finlex_path: str = _FINLEX_DEFAULT, *, kind: str = "all"
) -> Iterator[Tuple[str, str]]:
    """Yield ``(locator, source_role)`` for finlex PDF attachments, filtered by kind."""
    from farchive import Farchive

    fa = Farchive(finlex_path)
    try:
        for loc in fa.locators():
            if not loc.endswith(".pdf"):
                continue
            role = _classify(loc)
            if kind == "corrigenda" and role != "corrigendum":
                continue
            if kind == "media" and "corrigenda" in loc:
                continue
            yield loc, role
    finally:
        fa.close()


@dataclass(frozen=True, slots=True)
class ParseAttachmentsReport:
    pipeline_id: str
    scanned: int
    parsed: int
    cache_hits: int
    failed: int
    failures: Tuple[str, ...]


def parse_attachments_into_store(
    *,
    finlex_path: str = _FINLEX_DEFAULT,
    store_path: str = PARSED_STORE_DEFAULT,
    kind: str = "all",
    limit: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> ParseAttachmentsReport:
    """Parse finlex PDF attachments into the derived-IR store (idempotent).

    Every PDF goes through the UNIFIED adjudicated route (vision + reading-order,
    LLM-orchestrated). The route is probed ONCE and reused across the whole run;
    it RAISES ``ParseBackendUnavailable`` up front if the LLM server is down.
    """
    from lawvm.finland.source_document.pdf_profiles import load_manifestation_from_farchive

    spec = resolve_pipeline()  # raises ParseBackendUnavailable if the LLM server is down
    if verbose:
        print(f"  route: {spec.pipeline_id} ({spec.version})", flush=True)
    store = ParsedIrStore(store_path)
    scanned = parsed = cache_hits = failed = 0
    failures: List[str] = []
    try:
        for i, (loc, role) in enumerate(iter_finlex_pdf_locators(finlex_path, kind=kind)):
            if limit is not None and i >= limit:
                break
            scanned += 1
            try:
                m = load_manifestation_from_farchive(
                    loc, farchive_path=finlex_path, source_role=role
                )
                record = parse_and_cache(m, store, spec=spec, force=force)
            except Exception as exc:  # a bad attachment is a typed failure, not a crash
                failed += 1
                failures.append(f"{loc}: {type(exc).__name__}: {exc}")
                if verbose:
                    print(f"  FAIL {loc}: {type(exc).__name__}", flush=True)
                continue
            if record.cache_hit:
                cache_hits += 1
            else:
                parsed += 1
            if verbose and scanned % 100 == 0:
                print(f"  ...{scanned} scanned ({parsed} parsed, {cache_hits} cached, {failed} failed)", flush=True)
    finally:
        store.close()
    return ParseAttachmentsReport(
        pipeline_id=spec.pipeline_id,
        scanned=scanned,
        parsed=parsed,
        cache_hits=cache_hits,
        failed=failed,
        failures=tuple(failures),
    )


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-parse-attachments``."""
    from lawvm.finland.source_document.parsed_store import ParseBackendUnavailable

    store_path = args.store or PARSED_STORE_DEFAULT
    try:
        report = parse_attachments_into_store(
            finlex_path=args.finlex or _FINLEX_DEFAULT,
            store_path=store_path,
            kind=args.kind or "all",
            limit=args.limit,
            force=bool(args.force),
            verbose=bool(args.verbose),
        )
    except ParseBackendUnavailable as exc:
        raise SystemExit(f"fi-parse-attachments: {exc}") from exc
    print(f"finlex PDF attachments → derived IR store ({store_path}), route={report.pipeline_id}:")
    print(f"  scanned:    {report.scanned}")
    print(f"  parsed:     {report.parsed}")
    print(f"  cache hits: {report.cache_hits}")
    print(f"  failed:     {report.failed} (typed)")
    for f in report.failures[:10]:
        print(f"    - {f}")
