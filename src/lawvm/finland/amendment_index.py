#!/usr/bin/env python3
"""
Extract amendment-to-parent relationships from Finlex consolidated statutes.

This tool scans the Finlex consolidated ZIP file and builds a mapping between
amendment acts (muutoslait) and their parent statutes. This is a critical
discovery step for the LawVM Finland frontend, as it allows the grafter to
identify the full chain of patches required to reconstruct a Point-In-Time state.

In LawVM terms, this is part of the 'Source Fact Extraction' layer for Finland.
"""

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set, cast

import lxml.etree as etree
from functools import lru_cache

from lawvm.corpus_store import (
    CorpusStore,
    get_corpus_store,
)
from lawvm.finland.vts import extract_voimaantulo_repeals

# Pattern for /akn/fi/act/statute-consolidated/YEAR/NUMBER...
# or /akn/fi/act/statute/YEAR/NUMBER...
REF_PATTERN = re.compile(r'/akn/fi/act/statute(?:-consolidated)?/(\d{4})/(\d+(?:-\d+)?)')

# Canonical cache path — stored in .cache (gitignored)
_DEFAULT_CACHE_CSV = Path(".cache/finland/amendment_parents.csv")
_DEFAULT_CACHE_META = _DEFAULT_CACHE_CSV.with_suffix(".meta.json")
_CSV_HEADER = ["amendment_id", "parent_id", "edge_kind"]


def _append_amendment_index_diagnostic(
    diagnostics_out: list[dict[str, object]] | None,
    *,
    rule_id: str,
    phase: str,
    family: str,
    reason: str,
    detail: dict[str, object],
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": rule_id,
            "phase": phase,
            "family": family,
            "reason": reason,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            **detail,
        }
    )


def _make_statute_id(year: str, num_raw: str) -> str:
    """Normalize statute ID to YYYY/NUMBER format."""
    if '-' in num_raw:
        # Preserve sub-numbering for older statutes (e.g., 1889/39-001)
        return f"{year}/{num_raw}"
    return f"{year}/{int(num_raw)}"


def _normalize_source_citation_id(raw: str, source_year: int) -> str | None:
    """Normalize textual source citations like ``506/86`` or ``506/1986``."""
    raw = re.sub(r"\s+", "", (raw or ""))
    m = re.fullmatch(r"(\d{1,4})/(\d{2,4})", raw)
    if not m:
        return None
    left, right = m.groups()
    num = int(left)
    if len(right) == 4:
        return f"{right}/{num}"
    year_two = int(right)
    source_century = (source_year // 100) * 100
    full_year = source_century + year_two
    if full_year > source_year:
        full_year -= 100
    return f"{full_year}/{num}"


def _extract_explicit_cross_statute_vts_parents(
    xml_data: bytes,
    amendment_id: str,
    *,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> Set[str]:
    """Extract explicit parent statute IDs mentioned in VTS cross-statute clauses.

    This supplements the direct ``amendedBy``-based index with explicit source
    citations from entry-into-force / voimaantulo clauses such as:

      "Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan ..."
      "Tällä lailla kumotaan ... (785/1992) 11 § ..."

    The extractor is intentionally conservative: explicit statute citations only.
    """
    try:
        tree = etree.fromstring(xml_data)
    except etree.XMLSyntaxError as exc:
        _append_amendment_index_diagnostic(
            diagnostics_out,
            rule_id="fi_amendment_index_source_vts_xml_parse_failed",
            phase="parse",
            family="source_pathology",
            reason="Finland amendment index skipped source VTS extraction because source XML was not well-formed.",
            detail={
                "amendment_id": amendment_id,
                "edge_kind": "source_vts_explicit",
                "exception_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return set()

    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (ValueError, IndexError):
        return set()

    cited_ids: Set[str] = set()
    elements = tree.findall(".//{*}section") + tree.findall('.//{*}hcontainer[@eId="entryIntoForce"]')
    seen_texts: Set[str] = set()
    for el in elements:
        text = re.sub(r"\s+", " ", etree.tostring(el, method="text", encoding="unicode")).strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        lower = text.lower()
        is_relevant = (
            "kumotaan" in lower
            or ("jää" in lower and "lukuun ottamatta" in lower and "voimaan" in lower)
        )
        if not is_relevant:
            continue

        cut = re.search(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b", text, re.IGNORECASE)
        target_zone = text[:cut.start()] if cut else text
        for raw_citation in re.findall(r"\(\s*(\d{1,4}\s*/\s*\d{2,4})\s*\)", target_zone):
            norm = _normalize_source_citation_id(raw_citation, source_year)
            if norm and norm != amendment_id:
                cited_ids.add(norm)

    candidates: Set[str] = set()
    for parent_id in cited_ids:
        try:
            if extract_voimaantulo_repeals(xml_data, parent_id):
                candidates.add(parent_id)
        except Exception as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_parent_extraction_failed",
                phase="parse",
                family="source_pathology",
                reason="Finland amendment index skipped a candidate source VTS parent because extraction failed.",
                detail={
                    "amendment_id": amendment_id,
                    "parent_id": parent_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue
    return candidates

def build_amendment_index(
    cs: CorpusStore | None = None,
    consolidated_zip_path: Path | None = None,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> List[Tuple[str, str, str]]:
    """Scan consolidated statutes and extract (amendment_id, parent_id) pairs.

    ``cs`` may be a CorpusStore or None (auto-detects via get_corpus_store()).
    ``consolidated_zip_path`` is accepted for backward-compat CLI callers but
    is ignored — the Finland pipeline uses Farchive exclusively.

    Returns:
        List of sorted (amendment_id, parent_id) tuples.
    """
    if cs is None:
        cs = get_corpus_store()

    edges: Set[Tuple[str, str, str]] = set()

    # Use oracle_path_index() to enumerate sids, then read each statute via
    # its already-selected locator. Calling read_oracle(sid) here instead
    # would re-scan the locator table per statute, making this loop quadratic
    # over the corpus (~hours on the full Finlex corpus).
    oracle_index = cs.oracle_path_index()
    print(f"Scanning {len(oracle_index)} consolidated statutes for amendment metadata...")

    for n, sid in enumerate(sorted(oracle_index), start=1):
        if n % 5000 == 0:
            print(f"[amendment_index] consolidated scan {n}/{len(oracle_index)}")
        parts = sid.split("/", 1)
        if len(parts) != 2:
            continue
        year, num_raw = parts
        parent_id = _make_statute_id(year, num_raw)
        try:
            xml_data = cs.read_locator(oracle_index[sid])
            if xml_data is None:
                xml_data = cs.read_oracle(sid)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_oracle_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped consolidated oracle metadata because oracle XML bytes were missing.",
                    detail={
                        "statute_id": sid,
                        "parent_id": parent_id,
                        "edge_kind": "oracle_amendedBy",
                    },
                )
                continue
            root = etree.fromstring(xml_data)
            for ref_elem in cast(list, root.xpath('.//*[local-name()="amendedBy"]//*[local-name()="ref"]')):
                href = ref_elem.get('href', '')
                m = REF_PATTERN.search(href)
                if m:
                    amend_id = _make_statute_id(m.group(1), m.group(2))
                    if amend_id != parent_id:
                        edges.add((amend_id, parent_id, "oracle_amendedBy"))
        except (KeyError, OSError, etree.XMLSyntaxError, etree.XPathError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_oracle_artifact_skipped",
                phase="parse" if isinstance(exc, (etree.XMLSyntaxError, etree.XPathError)) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped consolidated oracle metadata because the artifact could not be read or parsed.",
                detail={
                    "statute_id": sid,
                    "parent_id": parent_id,
                    "edge_kind": "oracle_amendedBy",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    # Supplement from source VTS clauses. These are not represented by
    # consolidated amendedBy metadata when an amendment touches another statute
    # only via entry-into-force prose.
    statute_ids = sorted(cs.list_statute_ids())
    for n, amendment_id in enumerate(statute_ids, start=1):
        if n % 5000 == 0:
            print(f"[amendment_index] source VTS scan {n}/{len(statute_ids)}")
        try:
            xml_data = cs.read_source(amendment_id)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_source_vts_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped source VTS extraction because source XML bytes were missing.",
                    detail={
                        "amendment_id": amendment_id,
                        "edge_kind": "source_vts_explicit",
                    },
                )
                continue
            for parent_id in _extract_explicit_cross_statute_vts_parents(
                xml_data,
                amendment_id,
                diagnostics_out=diagnostics_out,
            ):
                edges.add((amendment_id, parent_id, "source_vts_explicit"))
        except (KeyError, OSError, etree.XMLSyntaxError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_artifact_skipped",
                phase="parse" if isinstance(exc, etree.XMLSyntaxError) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped source VTS extraction because the source artifact could not be read or parsed.",
                detail={
                    "amendment_id": amendment_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    return sorted(list(edges))

def _path_from_pathlike(value: object) -> Path | None:
    if isinstance(value, str):
        return Path(value)
    if isinstance(value, os.PathLike):
        return Path(cast(os.PathLike[str], value))
    return None


def _fingerprint_int(value: object) -> int:
    if isinstance(value, int | float | str | bytes | bytearray):
        return int(value)
    raise TypeError(f"Expected integer-like fingerprint field, got {type(value).__name__}")


def _corpus_source_fingerprint(cs: CorpusStore | None) -> dict[str, object] | None:
    """Return the backing farchive DB fingerprint, or None for unknown stores."""
    try:
        archive = getattr(cs, "_archive", None) if cs is not None else None
        candidates: list[object] = []
        if archive is not None:
            candidates.append(archive)
        if cs is not None:
            candidates.append(cs)

        path: Path | None = None
        for candidate in candidates:
            for attr in ("path", "_path", "db_path", "_db_path", "filename", "_filename"):
                value = getattr(candidate, attr, None)
                path = _path_from_pathlike(value)
                if path is not None:
                    break
            if path is not None:
                break

        if path is None:
            path = Path(os.environ.get("LAWVM_FARCHIVE_DB", "data/finlex.farchive"))
        if not path.exists():
            return None
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    except Exception:
        return None


def _fingerprints_equivalent(
    stored: object,
    current: dict[str, object] | None,
) -> bool:
    """True when two source fingerprints identify the same archive state.

    Path strings are compared after resolution so a representation change
    (relative vs absolute, symlink vs real path) alone never invalidates the
    cache — only an actual content change (size/mtime) forces the expensive
    full-corpus rebuild.
    """
    if stored == current:
        return True
    if not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    stored_map = cast(Dict[str, object], stored)
    if stored_map.get("size") != current.get("size"):
        return False
    if stored_map.get("mtime_ns") != current.get("mtime_ns"):
        return False
    stored_path = stored_map.get("path")
    current_path = current.get("path")
    if not isinstance(stored_path, str) or not isinstance(current_path, str):
        return False
    try:
        return Path(stored_path).resolve() == Path(current_path).resolve()
    except OSError:
        return False


def _read_csv_header(csv_path: Path) -> list[str]:
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            return next(csv.reader(f), [])
    except (OSError, StopIteration, csv.Error):
        return []


def _read_cache_meta(meta_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _cache_meta_payload(source_fingerprint: dict[str, object] | None) -> dict[str, object]:
    return {
        "schema": list(_CSV_HEADER),
        "source": source_fingerprint,
    }


def _write_cache_meta_atomic(
    meta_path: Path,
    source_fingerprint: dict[str, object] | None,
) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = meta_path.with_name(f".{meta_path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_cache_meta_payload(source_fingerprint), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, meta_path)


def _write_amendment_index_cache(
    csv_path: Path,
    edges: List[Tuple[str, str, str]],
    source_fingerprint: dict[str, object] | None,
) -> None:
    meta_path = csv_path.with_suffix(".meta.json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_tmp_path = csv_path.with_name(f".{csv_path.name}.tmp")
    with open(csv_tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        writer.writerows(edges)
    os.replace(csv_tmp_path, csv_path)
    _write_cache_meta_atomic(meta_path, source_fingerprint)


def ensure_amendment_index(
    cs: CorpusStore | None = None,
    csv_path: Path = _DEFAULT_CACHE_CSV,
) -> None:
    """Ensure amendment_parents.csv exists and is fresh for the source farchive.

    Transparent caching: rebuilds automatically when the farchive fingerprint
    differs from the sidecar metadata, when the CSV is missing, or when the CSV
    schema is stale.

    ``cs`` may be a CorpusStore (preferred) or None (auto-detects via
    get_corpus_store()).  For unknown backends the source staleness check is
    skipped and the CSV is used as long as it exists with the current schema.
    """
    should_close_cs = False
    if cs is None:
        cs = get_corpus_store()
        should_close_cs = True

    try:
        source_fingerprint = _corpus_source_fingerprint(cs)
        meta_path = csv_path.with_suffix(".meta.json")

        if csv_path.exists():
            header = _read_csv_header(csv_path)
            header_is_current = header[:3] == _CSV_HEADER
            if header_is_current and source_fingerprint is None:
                return
            if header_is_current:
                if not meta_path.exists():
                    # Adopt an existing current-schema CSV as fresh on first
                    # sidecar rollout. The full source scan is expensive, and
                    # the written fingerprint will catch subsequent DB changes.
                    _write_cache_meta_atomic(meta_path, source_fingerprint)
                    return
                meta = _read_cache_meta(meta_path)
                if meta is not None and _fingerprints_equivalent(
                    meta.get("source"), source_fingerprint
                ):
                    return
                print(f"[amendment_index] {csv_path} source fingerprint is stale — rebuilding")
            else:
                print(f"[amendment_index] {csv_path} schema is stale — rebuilding")
        else:
            print(f"[amendment_index] Building {csv_path}...")

        edges = build_amendment_index(cs=cs)
        _write_amendment_index_cache(csv_path, edges, source_fingerprint)
        print(f"[amendment_index] Wrote {len(edges)} mappings to {csv_path}")
    finally:
        if should_close_cs:
            cs.close()


def _default_source_cache_key() -> tuple[()] | tuple[str, int, int]:
    """Return an lru_cache key for the default corpus store source file."""
    try:
        cs = get_corpus_store()
    except (OSError, RuntimeError):
        return ()
    try:
        fingerprint = _corpus_source_fingerprint(cs)
        if fingerprint is None:
            return ()
        return (
            str(fingerprint["path"]),
            _fingerprint_int(fingerprint["size"]),
            _fingerprint_int(fingerprint["mtime_ns"]),
        )
    finally:
        cs.close()


@lru_cache(maxsize=256)
def _get_amendment_children_for_source_key(source_key: tuple[()] | tuple[str, int, int]) -> Dict[str, List[str]]:
    """Inner impl keyed on farchive fingerprint so DB changes invalidate in-process."""
    ensure_amendment_index(cs=None, csv_path=_DEFAULT_CACHE_CSV)
    mapping: Dict[str, List[str]] = {}
    with open(_DEFAULT_CACHE_CSV, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            mapping.setdefault(row[1], []).append(row[0])
    return mapping


@lru_cache(maxsize=256)
def _get_amendment_child_edges_for_source_key(
    source_key: tuple[()] | tuple[str, int, int],
) -> Dict[str, List[Tuple[str, str]]]:
    """Return cached {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    ensure_amendment_index(cs=None, csv_path=_DEFAULT_CACHE_CSV)
    mapping: Dict[str, List[Tuple[str, str]]] = {}
    with open(_DEFAULT_CACHE_CSV, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            edge_kind = row[2] if len(row) >= 3 and row[2] else "oracle_amendedBy"
            mapping.setdefault(row[1], []).append((row[0], edge_kind))
    return mapping


def get_amendment_children() -> Dict[str, List[str]]:
    """Return {parent_statute_id: [amendment_id, ...]} mapping.

    Transparent caching: on-disk CSV rebuilt from the corpus store's farchive
    fingerprint when the DB changes or the cache is missing. In-process result
    is keyed on the same fingerprint, so callers never need to know about the
    backing CSV.
    """
    return _get_amendment_children_for_source_key(_default_source_cache_key())


def get_amendment_child_edges() -> Dict[str, List[Tuple[str, str]]]:
    """Return {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    return _get_amendment_child_edges_for_source_key(_default_source_cache_key())


def main():
    parser = argparse.ArgumentParser(description="Extract amendment-to-parent mapping from Finlex.")
    parser.add_argument("--out", type=Path, default=Path("data/finland/amendment_parents.csv"),
                        help="Output CSV path")
    args = parser.parse_args()

    try:
        edges = build_amendment_index()

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADER)
            writer.writerows(edges)

        print(f"Successfully wrote {len(edges)} mappings to {args.out}")
    except (FileNotFoundError, OSError, etree.XMLSyntaxError) as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
