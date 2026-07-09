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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from lawvm.finland.source_document.parsed_store import (
    PARSED_STORE_DEFAULT,
    STRUCT_BUILD_MODALITIES,
    ParsedIrStore,
    parse_and_cache,
    parse_struct_and_cache,
    resolve_pipeline,
)

_FINLEX_DEFAULT = "data/finlex.farchive"

# Default bounded in-flight window: keep a short queue of whole-PDF requests
# saturating the single inference server without overrunning it. Concurrency is
# PER-PDF (each PDF's pages stay sequential-with-context inside parse_*_and_cache);
# only distinct PDFs run in parallel.
_DEFAULT_WORKERS = 6


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


def _parse_one(
    loc: str,
    role: str,
    *,
    finlex_path: str,
    store_path: str,
    spec: object,
    force: bool,
    modality: str,
) -> Tuple[str, str, Optional[str]]:
    """Parse ONE attachment → ``(locator, status, detail)``; never raises.

    Opens its OWN derived-store connection in THIS worker thread. Farchive is
    SQLite-backed with ``check_same_thread=True`` (documented not-thread-safe):
    the intended concurrency model is one connection per thread over a WAL DB
    (``busy_timeout`` serializes concurrent writers), NOT a shared connection. The
    ``ParsedRecord`` returned by ``parse_*_and_cache`` is an in-memory value, so it
    outlives the ``store.close()`` in the ``finally``. ``status`` is ``"hit"`` /
    ``"parsed"`` / ``"failed"``; a bad attachment is a typed failure (AGENTS.md
    §1.8), not a crash that would sink the pool.
    """
    from lawvm.finland.source_document.pdf_profiles import load_manifestation_from_farchive

    store = ParsedIrStore(store_path)
    try:
        m = load_manifestation_from_farchive(loc, farchive_path=finlex_path, source_role=role)
        if modality in STRUCT_BUILD_MODALITIES:
            record = parse_struct_and_cache(m, store, spec=spec, force=force)  # ty: ignore[invalid-argument-type]
        else:
            record = parse_and_cache(m, store, spec=spec, force=force)  # ty: ignore[invalid-argument-type]
    except Exception as exc:  # a bad attachment is a typed failure, not a crash
        return (loc, "failed", f"{type(exc).__name__}: {exc}")
    finally:
        store.close()
    return (loc, "hit" if record.cache_hit else "parsed", None)


def parse_attachments_into_store(
    *,
    finlex_path: str = _FINLEX_DEFAULT,
    store_path: str = PARSED_STORE_DEFAULT,
    kind: str = "all",
    limit: int | None = None,
    force: bool = False,
    verbose: bool = False,
    modality: str = "struct_span",
    workers: int = _DEFAULT_WORKERS,
) -> ParseAttachmentsReport:
    """Parse finlex PDF attachments into the derived-IR store (idempotent).

    Every PDF goes through the UNIFIED adjudicated route (vision + reading-order,
    LLM-orchestrated). The route is probed ONCE and reused across the whole run;
    it RAISES ``ParseBackendUnavailable`` up front if the LLM server is down.

    ``workers`` whole PDFs are kept in flight at once (bounded per-PDF concurrency
    saturating the single inference server); each PDF's pages stay
    sequential-with-context inside ``parse_*_and_cache``. ``modality`` selects the
    lane (``struct_span`` default → the v2 build-script; any ``struct_*`` or the
    legacy flat ``full_transcription`` / ``span_copy`` / ``auto``).
    """
    spec = resolve_pipeline(transcription_modality=modality)  # raises if backend down
    if verbose:
        print(f"  route: {spec.pipeline_id} ({spec.version}) | workers={workers}", flush=True)

    items: List[Tuple[str, str]] = []
    for i, (loc, role) in enumerate(iter_finlex_pdf_locators(finlex_path, kind=kind)):
        if limit is not None and i >= limit:
            break
        items.append((loc, role))

    scanned = parsed = cache_hits = failed = 0
    failures: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _parse_one, loc, role,
                finlex_path=finlex_path, store_path=store_path, spec=spec,
                force=force, modality=modality,
            ): loc
            for loc, role in items
        }
        for fut in as_completed(futures):
            _loc, status, detail = fut.result()
            scanned += 1
            if status == "failed":
                failed += 1
                failures.append(f"{_loc}: {detail}")
                if verbose:
                    print(f"  FAIL {_loc}: {detail}", flush=True)
            elif status == "hit":
                cache_hits += 1
            else:
                parsed += 1
            if verbose and scanned % 100 == 0:
                print(f"  ...{scanned}/{len(items)} ({parsed} parsed, {cache_hits} cached, {failed} failed)", flush=True)
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
            modality=args.modality or "struct_span",
            workers=args.workers if args.workers else _DEFAULT_WORKERS,
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
