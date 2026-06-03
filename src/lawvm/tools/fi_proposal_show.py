"""lawvm fi-proposal-show HE_ID — per-HE structural overview.

Shows metadata + optionally atoms, law_refs, and signatures for one HE.
Default output: metadata only (no full body text). Use --include-atoms etc.
to pull in the corresponding projection tables.

Usage:
    lawvm fi-proposal-show "HE 98/1996 vp"
    lawvm fi-proposal-show "HE 98/1996 vp" --include-atoms
    lawvm fi-proposal-show "HE 98/1996 vp" --include-law-refs --include-signatures
    lawvm fi-proposal-show "HE 98/1996 vp" -o json

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (currently only 'fi' supported; default 'fi')
  -o {table|json|jsonl}  output format (default: table)
  --data-dir PATH   override default data directory (default: data/fi/v1)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------


def _default_data_dir() -> str:
    return "data/fi/v1"


def _check_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def _find_source(data_dir: str, table_name: str) -> Optional[Path]:
    """Return parquet or jsonl path for a given table, preferring parquet."""
    p = Path(data_dir) / f"{table_name}.parquet"
    if p.exists():
        return p
    j = Path(data_dir) / f"{table_name}.jsonl"
    if j.exists():
        return j
    return None


def _source_expr(path: Path) -> str:
    if path.suffix.lower() == ".parquet":
        return f"read_parquet('{path}')"
    return f"read_json_auto('{path}')"


def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool, list)):
        return v
    return str(v)


# ---------------------------------------------------------------------------
# HE-ID normalisation
# ---------------------------------------------------------------------------


def _normalise_he_id(he_id: str) -> str:
    """Normalise an HE identifier for query matching.

    Accepts: "HE 98/1996 vp", "HE 98/1996", etc.
    Returns the trimmed form used in a LIKE/equality filter.
    """
    return he_id.strip()


def _build_corpus_query(
    corpus_source: str,
    he_id: str,
) -> str:
    """Build DuckDB SQL to fetch the corpus row for one HE_ID."""
    safe = he_id.replace("'", "''")
    return (
        f"SELECT * FROM {corpus_source} "
        f"WHERE he_id = '{safe}' OR lower(he_id) = lower('{safe}') "
        f"ORDER BY he_year, he_number "
        "LIMIT 10"
    )


def _build_atoms_query(
    atoms_source: str,
    he_id: str,
    limit: Optional[int] = None,
) -> str:
    safe = he_id.replace("'", "''")
    limit_clause = f" LIMIT {limit}" if limit else ""
    return (
        f"SELECT he_id, atom_id, parent_atom_id, atom_type, seq, num, heading, "
        f"char_count, source_span_file FROM {atoms_source} "
        f"WHERE he_id = '{safe}'"
        f" ORDER BY seq"
        f"{limit_clause}"
    )


def _build_law_refs_query(
    refs_source: str,
    he_id: str,
    limit: Optional[int] = None,
) -> str:
    safe = he_id.replace("'", "''")
    limit_clause = f" LIMIT {limit}" if limit else ""
    return (
        f"SELECT he_id, source_provision_ref_str, target_statute_id, "
        f"target_provision_ref_str, cite_kind, cite_confidence, phrase_lemma "
        f"FROM {refs_source} "
        f"WHERE he_id = '{safe}'"
        f" ORDER BY source_provision_ref_str"
        f"{limit_clause}"
    )


def _build_signatures_query(
    sigs_source: str,
    he_id: str,
) -> str:
    safe = he_id.replace("'", "''")
    return (
        f"SELECT he_id, role, person, signature_order FROM {sigs_source} "
        f"WHERE he_id = '{safe}' ORDER BY signature_order"
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_kv(label: str, value: Any) -> str:
    return f"  {label:<30s} {value}"


def _format_corpus_row(row: tuple, columns: List[str]) -> str:
    lines = [""]
    row_dict = dict(zip(columns, row, strict=True))
    for col in columns:
        v = row_dict.get(col)
        lines.append(_format_kv(col, v if v is not None else ""))
    return "\n".join(lines)


def _format_table(columns: List[str], rows: List[tuple], max_col_width: int = 60) -> str:
    if not rows:
        return "  (0 rows)"
    str_rows = [[str(v) if v is not None else "" for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(val), max_col_width))
    header = "  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    separator = "  " + "  ".join("-" * w for w in widths)
    lines = [header, separator]
    for row in str_rows:
        lines.append("  " + "  ".join(
            val[:max_col_width].ljust(w) for val, w in zip(row, widths, strict=True)
        ))
    lines.append(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_fi_proposal_show(
    he_id: str,
    *,
    include_atoms: bool = False,
    include_law_refs: bool = False,
    include_signatures: bool = False,
    limit: Optional[int] = None,
    data_dir: str = "data/fi/v1",
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Show per-HE structural overview from the fi_he_* projections."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm fi-proposal-show' only supports 'fi'; got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    corpus_path = _find_source(data_dir, "fi_he_corpus")
    if corpus_path is None:
        print(
            f"No fi_he_corpus.parquet or fi_he_corpus.jsonl found in {data_dir}/\n"
            "Run 'lawvm sync-fi-proposals' first to generate projection files.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _check_duckdb():
        print(
            "error: duckdb is not installed. Install with: uv pip install duckdb",
            file=sys.stderr,
        )
        sys.exit(1)

    import duckdb

    he_id_norm = _normalise_he_id(he_id)

    con = duckdb.connect(":memory:")

    # --- Corpus row ---
    corpus_expr = _source_expr(corpus_path)
    corpus_query = _build_corpus_query(corpus_expr, he_id_norm)
    corpus_result = con.execute(corpus_query)
    corpus_cols = [d[0] for d in corpus_result.description]
    corpus_rows = corpus_result.fetchall()

    if not corpus_rows:
        print(f"No HE found for id {he_id_norm!r} in {data_dir}/fi_he_corpus", file=sys.stderr)
        sys.exit(1)

    out: Dict[str, Any] = {}

    if output_format == "json":
        row = corpus_rows[0]
        out["corpus"] = dict(zip(corpus_cols, [_json_safe(v) for v in row], strict=True))
    elif output_format == "jsonl":
        row = corpus_rows[0]
        print(json.dumps(
            dict(zip(corpus_cols, [_json_safe(v) for v in row], strict=True)),
            ensure_ascii=False,
        ))
    else:
        print(f"\nHE: {he_id_norm}")
        print(_format_corpus_row(corpus_rows[0], corpus_cols))

    # Determine canonical he_id from the found row for cross-table lookups
    row_dict = dict(zip(corpus_cols, corpus_rows[0], strict=True))
    canonical_he_id: str = str(row_dict.get("he_id", he_id_norm))

    # --- Optional: atoms ---
    if include_atoms:
        atoms_path = _find_source(data_dir, "fi_he_atoms")
        if atoms_path is None:
            print(
                f"  Warning: fi_he_atoms not found in {data_dir}/; "
                "run sync-fi-proposals --full to rebuild",
                file=sys.stderr,
            )
        else:
            atoms_expr = _source_expr(atoms_path)
            atoms_q = _build_atoms_query(atoms_expr, canonical_he_id, limit=limit)
            atoms_res = con.execute(atoms_q)
            atoms_cols = [d[0] for d in atoms_res.description]
            atoms_rows = atoms_res.fetchall()
            if output_format == "json":
                out["atoms"] = [
                    dict(zip(atoms_cols, [_json_safe(v) for v in r], strict=True))
                    for r in atoms_rows
                ]
            elif output_format == "jsonl":
                for r in atoms_rows:
                    print(json.dumps(
                        {"_table": "atoms",
                         **dict(zip(atoms_cols, [_json_safe(v) for v in r], strict=True))},
                        ensure_ascii=False,
                    ))
            else:
                print(f"\nAtoms ({len(atoms_rows)} rows):")
                print(_format_table(atoms_cols, atoms_rows))

    # --- Optional: law_refs ---
    if include_law_refs:
        refs_path = _find_source(data_dir, "fi_he_law_refs")
        if refs_path is None:
            print(
                f"  Warning: fi_he_law_refs not found in {data_dir}/; "
                "run sync-fi-proposals --full to rebuild",
                file=sys.stderr,
            )
        else:
            refs_expr = _source_expr(refs_path)
            refs_q = _build_law_refs_query(refs_expr, canonical_he_id, limit=limit)
            refs_res = con.execute(refs_q)
            refs_cols = [d[0] for d in refs_res.description]
            refs_rows = refs_res.fetchall()
            if output_format == "json":
                out["law_refs"] = [
                    dict(zip(refs_cols, [_json_safe(v) for v in r], strict=True))
                    for r in refs_rows
                ]
            elif output_format == "jsonl":
                for r in refs_rows:
                    print(json.dumps(
                        {"_table": "law_refs",
                         **dict(zip(refs_cols, [_json_safe(v) for v in r], strict=True))},
                        ensure_ascii=False,
                    ))
            else:
                print(f"\nLaw refs ({len(refs_rows)} rows):")
                print(_format_table(refs_cols, refs_rows))

    # --- Optional: signatures ---
    if include_signatures:
        sigs_path = _find_source(data_dir, "fi_he_signatures")
        if sigs_path is None:
            print(
                f"  Warning: fi_he_signatures not found in {data_dir}/; "
                "run sync-fi-proposals --full to rebuild",
                file=sys.stderr,
            )
        else:
            sigs_expr = _source_expr(sigs_path)
            sigs_q = _build_signatures_query(sigs_expr, canonical_he_id)
            sigs_res = con.execute(sigs_q)
            sigs_cols = [d[0] for d in sigs_res.description]
            sigs_rows = sigs_res.fetchall()
            if output_format == "json":
                out["signatures"] = [
                    dict(zip(sigs_cols, [_json_safe(v) for v in r], strict=True))
                    for r in sigs_rows
                ]
            elif output_format == "jsonl":
                for r in sigs_rows:
                    print(json.dumps(
                        {"_table": "signatures",
                         **dict(zip(sigs_cols, [_json_safe(v) for v in r], strict=True))},
                        ensure_ascii=False,
                    ))
            else:
                print(f"\nSignatures ({len(sigs_rows)} rows):")
                print(_format_table(sigs_cols, sigs_rows))

    con.close()

    if output_format == "json":
        print(json.dumps(out, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    run_fi_proposal_show(
        he_id=getattr(args, "he_id"),
        include_atoms=getattr(args, "include_atoms", False),
        include_law_refs=getattr(args, "include_law_refs", False),
        include_signatures=getattr(args, "include_signatures", False),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", "data/fi/v1"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )
