"""lawvm export — batch graph export for Finnish statutes.

Exports the compiled statute graph to graph DB import formats.

Outputs (Neo4j CSV bulk import format):
  nodes_statutes.csv     statute_id, title, statute_type, year, n_amendments
  rels_amends.csv        amendment_id, parent_id
  rels_delegates.csv     from_statute, from_section, to_type, eid, match_text
  rels_cites.csv         from_statute, to_statute, edge_type, from_section, to_section, count

Usage:
    lawvm export --neo4j <output_dir>
    lawvm export --neo4j <output_dir> --corpus .tmp/migration/expanded_batch_test_list.csv
    lawvm export --neo4j <output_dir> --limit 100
    lawvm export --jsonld statute_graph.jsonld

Data sources (all from farchive, no replay needed):
  data/finlex.farchive               — statute XML (title, typeStatute) and oracle consolidated
  data/finland/amendment_parents.csv — amendment→parent linkages
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List


# ---------------------------------------------------------------------------
# Corpus reader
# ---------------------------------------------------------------------------

STANDARD_CORPUS_CSV = Path(".tmp/batch_test_list.csv")


def _read_corpus(csv_path: Path) -> List[str]:
    """Return list of statute_ids from a corpus CSV (format: N,YYYY/NNN)."""
    ids = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and re.match(r'^\d{4}/\d+$', row[1]):
                ids.append(row[1])
    return ids


# ---------------------------------------------------------------------------
# Neo4j CSV export
# ---------------------------------------------------------------------------

def export_neo4j(output_dir: Path, corpus: List[str], verbose: bool = False) -> None:
    """Write Neo4j bulk import CSVs to output_dir."""
    from lawvm.graph_build import build_corpus_graph

    output_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print("  Building corpus graph (lightweight, no replay)...", file=sys.stderr)
    cg = asyncio.run(build_corpus_graph(corpus, with_timelines=False))

    # Amendment index: amend→parent
    amend_to_parent: Dict[str, str] = {}
    for parent, amends in cg.amendment_index.items():
        for a in amends:
            amend_to_parent[a] = parent

    corpus_set = set(corpus)

    # Statute nodes
    statute_rows = []
    for sid in corpus:
        meta = cg.statute_meta.get(sid)
        if meta is None:
            continue
        year = sid.split("/")[0]
        statute_rows.append({
            "statute_id": sid,
            "title": meta["title"],
            "statute_type": meta["statute_type"],
            "year": year,
            "n_amendments": len(cg.amendment_index.get(sid, [])),
        })

    _write_csv(
        output_dir / "nodes_statutes.csv",
        ["statute_id", "title", "statute_type", "year", "n_amendments"],
        statute_rows,
    )
    print(f"  {len(statute_rows):>6} statute nodes → {output_dir}/nodes_statutes.csv")

    # Amendment edges
    amend_rows = [
        {"amendment_id": a, "parent_id": p}
        for a, p in amend_to_parent.items()
        if a in corpus_set or p in corpus_set
    ]
    _write_csv(
        output_dir / "rels_amends.csv",
        ["amendment_id", "parent_id"],
        amend_rows,
    )
    print(f"  {len(amend_rows):>6} amends edges → {output_dir}/rels_amends.csv")

    # Delegation edges
    delegate_rows = [
        {
            "from_statute": e.statute_id,
            "from_section": e.section,
            "delegation_type": e.delegation_type,
            "eid": e.eid,
        }
        for e in cg.delegations
    ]
    _write_csv(
        output_dir / "rels_delegates.csv",
        ["from_statute", "from_section", "delegation_type", "eid"],
        delegate_rows,
    )
    print(f"  {len(delegate_rows):>6} delegation edges → {output_dir}/rels_delegates.csv")

    # Citation edges
    cite_rows = [
        {
            "from_statute": e.source_statute_id,
            "to_statute": e.target_statute_id,
            "edge_type": e.edge_type,
            "from_section": e.source_section,
            "to_section": e.target_section,
            "count": e.count,
        }
        for e in cg.citations
    ]
    _write_csv(
        output_dir / "rels_cites.csv",
        ["from_statute", "to_statute", "edge_type", "from_section", "to_section", "count"],
        cite_rows,
    )
    print(f"  {len(cite_rows):>6} citation edges → {output_dir}/rels_cites.csv")


def _write_csv(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# JSON-LD export
# ---------------------------------------------------------------------------

def export_jsonld(output_file: Path, corpus: List[str], verbose: bool = False) -> None:
    """Write JSON-LD statute graph to output_file (ELI-compatible vocabulary)."""
    from lawvm.graph_build import build_corpus_graph

    if verbose:
        print("  Building corpus graph (lightweight, no replay)...", file=sys.stderr)
    cg = asyncio.run(build_corpus_graph(corpus, with_timelines=False))

    statutes = []
    for sid in corpus:
        meta = cg.statute_meta.get(sid)
        if meta is None:
            continue
        year, num = sid.split("/")
        obj = {
            "@type": "eli:LegalResource",
            "@id": f"http://data.finlex.fi/eli/sd/{year}/{num}",
            "eli:id_local": sid,
            "dcterms:title": meta["title"],
            "eli:type_document": meta["statute_type"],
            "dcterms:issued": year,
            "eli:is_realized_by": [],
        }
        for amend in cg.amendment_index.get(sid, []):
            obj["eli:is_realized_by"].append({
                "@type": "eli:LegalExpression",
                "eli:id_local": amend,
            })
        statutes.append(obj)

    doc = {
        "@context": {
            "eli": "http://data.europa.eu/eli/ontology#",
            "dcterms": "http://purl.org/dc/terms/",
        },
        "@graph": statutes,
    }
    output_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {len(statutes)} statute resources → {output_file}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    corpus_path = Path(args.corpus) if args.corpus else STANDARD_CORPUS_CSV
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    corpus = _read_corpus(corpus_path)
    if args.limit:
        corpus = corpus[: args.limit]

    print(f"Corpus: {len(corpus)} statutes from {corpus_path}")

    if args.neo4j:
        print(f"Exporting Neo4j CSV to {args.neo4j}/...")
        export_neo4j(Path(args.neo4j), corpus, verbose=args.verbose)
        print("Done.")

    if args.jsonld:
        print(f"Exporting JSON-LD to {args.jsonld}...")
        export_jsonld(Path(args.jsonld), corpus, verbose=args.verbose)
        print("Done.")

    if not args.neo4j and not args.jsonld:
        print("ERROR: specify --neo4j <dir> and/or --jsonld <file>", file=sys.stderr)
        sys.exit(1)
