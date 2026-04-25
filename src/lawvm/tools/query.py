"""lawvm query — run graph queries against a pre-built artifact (A3).

Reads from a directory produced by `lawvm build`. Much faster than `lawvm graph`
because nothing is built on-the-fly.

Usage:
    lawvm query --graph .tmp/corpus_graph/ --reverse-cites 2002/738
    lawvm query --graph .tmp/corpus_graph/ --delegates 2009/953
    lawvm query --graph .tmp/corpus_graph/ --silent-breakage 2002/738
    lawvm query --graph .tmp/corpus_graph/ --breakage-report 2002/738 2009/953
    lawvm query --graph .tmp/corpus_graph/ --stats
    lawvm query --graph .tmp/corpus_graph/ --statute 2009/953
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import argparse


# ---------------------------------------------------------------------------
# Artifact loader
# ---------------------------------------------------------------------------

@dataclass
class ArtifactGraph:
    """Lightweight in-memory view of a serialized lawvm build artifact."""
    meta: dict
    statute_meta: Dict[str, dict]
    amendment_index: Dict[str, List[str]]
    citations: list         # List[dict] — raw dicts from citations.jsonl
    delegations: list       # List[dict] — raw dicts from delegations.jsonl

    def reverse_citations(self, sid: str) -> List[dict]:
        return [c for c in self.citations if c.get("target_statute_id") == sid]

    def delegations_for(self, sid: str, section: str = "") -> List[dict]:
        edges = [d for d in self.delegations if d.get("statute_id") == sid]
        if section:
            edges = [d for d in edges if d.get("section") == section]
        return edges

    def affecting_acts(self, sid: str) -> List[str]:
        return list(self.amendment_index.get(sid, []))

    def silent_breakage(self, sid: str, target_section: str = "") -> List[dict]:
        edges = [c for c in self.citations
                 if c.get("target_statute_id") == sid and c.get("edge_type") == "CITES"]
        if target_section:
            edges = [e for e in edges if target_section in (e.get("target_section") or "")]
        # Normalize to CorpusGraph.silent_breakage() key names
        return [
            {
                "citing_statute": e.get("source_statute_id", ""),
                "citing_section": e.get("source_section", ""),
                "target_section": e.get("target_section", ""),
                "count": e.get("count", 1),
                "active_at_date": None,
            }
            for e in edges
        ]

    def breakage_report(self, changed_statutes: List[str]) -> List[dict]:
        """Push-based: given a list of recently-changed statute IDs, return all
        citation edges potentially invalidated by those changes."""
        results: List[dict] = []
        for sid in changed_statutes:
            for row in self.silent_breakage(sid):
                results.append({"changed_statute": sid, **row})
        return results

    def targets_of_act(self, act_id: str) -> List[str]:
        """Return all statute IDs that act_id has amended (reverse amendment_index)."""
        return sorted(
            parent for parent, amenders in self.amendment_index.items()
            if act_id in amenders
        )

    def issued_under(self, sid: str) -> List[dict]:
        """Return decrees (asetukset) issued under sid (ISSUED_UNDER edges targeting sid)."""
        return [c for c in self.citations
                if c.get("target_statute_id") == sid and c.get("edge_type") == "ISSUED_UNDER"]

    def eu_refs(self, sid: str) -> List[dict]:
        """Return EU cross-reference edges sourced from sid (FI→EU)."""
        return [c for c in self.citations
                if c.get("source_statute_id") == sid
                and (c.get("target_statute_id") or "").startswith("eu/")]

    @staticmethod
    def _is_permissive(clause: dict) -> bool:
        """Return True if delegation clause appears permissive (voidaan/voi/saa/tarvittaessa)."""
        text = (clause.get("match_text", "") + " " + clause.get("quote", "")).lower()
        return any(p in text for p in ["voidaan", " voi ", " saa ", "tarvittaessa"])

    def missing_decrees(self, sids: List[str]) -> List[dict]:
        """For each statute in sids, return mandatory delegation clauses with no ISSUED_UNDER decree.

        "Mandatory" = not permissive (no voidaan/voi/saa/tarvittaessa in clause text).
        Caveat: section-level matching not available — if any decree is ISSUED_UNDER sid,
        all clauses are considered covered. This is a statute-level approximation.

        Returns list of {"statute_id", "section", "delegation_type", "match_text"}
        for statutes with mandatory clauses but NO decrees at all.
        """
        results = []
        for sid in sids:
            clauses = self.delegations_for(sid)
            mandatory = [c for c in clauses if not self._is_permissive(c)]
            if not mandatory:
                continue
            has_decrees = bool(self.issued_under(sid))
            if not has_decrees:
                for c in mandatory:
                    results.append({
                        "statute_id": sid,
                        "section": c.get("section", ""),
                        "delegation_type": c.get("delegation_type", ""),
                        "match_text": c.get("match_text", "")[:120],
                    })
        return results

    def repeal_cascade(self, sid: str) -> dict:
        """Show which decrees become orphaned if sid is repealed.

        Returns {"decrees": [...], "repealed_decrees": [...]} where
        decrees is all ISSUED_UNDER decrees, and repealed_decrees is the
        subset that already has a REPEALS edge in the graph (already orphaned
        or pre-repealed). The ones NOT in repealed_decrees would become
        newly orphaned when sid is repealed.
        """
        decrees = self.issued_under(sid)
        # Build set of decree IDs that have been explicitly repealed in the graph
        repealed_ids = {
            c.get("target_statute_id")
            for c in self.citations
            if c.get("edge_type") == "REPEALS"
        }
        alive = [d for d in decrees if d.get("source_statute_id") not in repealed_ids]
        already_dead = [d for d in decrees if d.get("source_statute_id") in repealed_ids]
        return {"all_decrees": decrees, "alive": alive, "already_repealed": already_dead}

    def delegation_chain(self, sid: str) -> dict:
        """Return delegation clauses + decrees issued under sid, as a combined view.

        Returns {"clauses": [...], "decrees": [...], "unexercised": [...]} where
        clauses are delegation edges, decrees are ISSUED_UNDER edges targeting sid,
        and unexercised is the subset of clauses whose section number is not covered
        by any decree's target_section (Phase 8.4: preamble-parsed section authority).
        """
        clauses = self.delegations_for(sid)
        decrees = self.issued_under(sid)

        # Build set of sections covered by decrees (target_section from preamble parsing)
        covered_sections: set = set()
        for d in decrees:
            ts = d.get("target_section", "")
            if ts:
                for s in ts.split(","):
                    covered_sections.add(s.strip())

        # A clause is unexercised if its section is NOT in covered_sections
        # (only when at least one decree has section info; otherwise fall back to
        # statute-level: if any decree exists, all clauses are tentatively "exercised")
        if covered_sections:
            unexercised = [c for c in clauses if c.get("section", "") not in covered_sections]
        else:
            # No preamble-parsed section data: use statute-level approximation
            unexercised = clauses if not decrees else []

        return {"clauses": clauses, "decrees": decrees, "unexercised": unexercised}


def load_artifact(graph_dir: Path) -> ArtifactGraph:
    """Load a pre-built lawvm artifact from disk."""
    meta_path = graph_dir / "meta.json"
    if not meta_path.exists():
        print(f"ERROR: not a lawvm artifact directory: {graph_dir}", file=sys.stderr)
        print("  (missing meta.json — run `lawvm build` first)", file=sys.stderr)
        sys.exit(1)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    schema = meta.get("schema_version", "?")
    if schema != "11.0":
        print(f"WARNING: artifact schema {schema!r} — expected 11.0; may be incompatible",
              file=sys.stderr)

    statutes_path = graph_dir / "statutes.json"
    statute_meta = {}
    if statutes_path.exists():
        with open(statutes_path, encoding="utf-8") as f:
            statute_meta = json.load(f)

    amendments_path = graph_dir / "amendments.json"
    amendment_index: dict = {}
    if amendments_path.exists():
        with open(amendments_path, encoding="utf-8") as f:
            amendment_index = json.load(f)

    citations: list = []
    cite_path = graph_dir / "citations.jsonl"
    if cite_path.exists():
        with open(cite_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    citations.append(json.loads(line))

    delegations: list = []
    delg_path = graph_dir / "delegations.jsonl"
    if delg_path.exists():
        with open(delg_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    delegations.append(json.loads(line))

    return ArtifactGraph(
        meta=meta,
        statute_meta=statute_meta,
        amendment_index=amendment_index,
        citations=citations,
        delegations=delegations,
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_meta(ag: ArtifactGraph) -> None:
    m = ag.meta
    print(f"Artifact: schema={m.get('schema_version')}  "
          f"jurisdiction={m.get('jurisdiction', '?')}  "
          f"built={m.get('built_at', '?')[:19]}  "
          f"commit={m.get('lawvm_commit', '?')}")
    print(f"  corpus_size={m.get('corpus_size')}  "
          f"with_timelines={m.get('with_timelines', False)}")
    print(f"  citations={len(ag.citations)}  "
          f"delegations={len(ag.delegations)}  "
          f"amendment_links={sum(len(v) for v in ag.amendment_index.values())}")


def _parse_year(sid: str) -> Optional[int]:
    """Extract enactment year from statute ID like '2002/738-000' or '1734/1-000'."""
    try:
        return int(sid.split("/")[0])
    except (ValueError, IndexError):
        return None


def _decade(year: int) -> str:
    return f"{(year // 10) * 10}s"


_TYPE_LABEL = {
    "act": "laki",
    "decree": "asetus",
    "decision": "päätös",
    "announcement": "ilmoitus",
    "rules-of-procedure": "ohjesääntö",
    "official-regulation": "määräys",
    "budget": "budjetti",
    "letter": "kirje",
}


def _print_corpus_stats(ag: ArtifactGraph) -> None:
    """Print corpus breakdown by decade × statute_type with amendment distribution."""
    from collections import defaultdict

    # statute_id → amendment count
    amend_count: Dict[str, int] = {sid: len(v) for sid, v in ag.amendment_index.items()}

    # Group by (decade, type)
    by_decade_type: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    for sid, info in ag.statute_meta.items():
        year = _parse_year(sid)
        if year is None:
            continue
        decade = _decade(year)
        stype = info.get("statute_type", "?")
        n_amend = amend_count.get(sid, 0)
        by_decade_type[decade][stype].append(n_amend)

    # Aggregate across all types for totals
    totals: Dict[str, List[int]] = defaultdict(list)
    for decade, by_type in by_decade_type.items():
        for counts in by_type.values():
            totals[decade].extend(counts)

    total_all = sum(len(v) for v in totals.values())
    zero_all = sum(1 for v in totals.values() for c in v if c == 0)

    print(f"\n=== CORPUS STATS ({total_all} statutes) ===")
    print(f"  0-amendment: {zero_all} ({zero_all/total_all:.0%})  "
          f"≥1-amendment: {total_all - zero_all} ({(total_all - zero_all)/total_all:.0%})")

    # Summary by decade
    print(f"\n{'Decade':<8}  {'N':>6}  {'0-amend':>9}  {'AvgAmend':>9}  {'MaxAmend':>9}")
    print("-" * 52)
    for decade in sorted(totals.keys()):
        counts = totals[decade]
        n = len(counts)
        zero = sum(1 for c in counts if c == 0)
        avg = sum(counts) / n if n else 0
        mx = max(counts) if counts else 0
        print(f"{decade:<8}  {n:>6}  {zero:>7} ({zero*100//n:>2}%)  {avg:>9.1f}  {mx:>9}")

    # Type breakdown across all decades
    all_by_type: Dict[str, List[int]] = defaultdict(list)
    for by_type in by_decade_type.values():
        for stype, counts in by_type.items():
            all_by_type[stype].extend(counts)

    print(f"\n{'Type':<18}  {'N':>6}  {'0-amend':>9}  {'AvgAmend':>9}")
    print("-" * 50)
    for stype in sorted(all_by_type, key=lambda t: -len(all_by_type[t])):
        counts = all_by_type[stype]
        n = len(counts)
        zero = sum(1 for c in counts if c == 0)
        avg = sum(counts) / n if n else 0
        label = _TYPE_LABEL.get(stype, stype)
        print(f"{label:<18}  {n:>6}  {zero:>7} ({zero*100//n:>2}%)  {avg:>9.1f}")


def _print_stats(ag: ArtifactGraph) -> None:
    _print_meta(ag)
    eu_cites = sum(1 for c in ag.citations
                   if (c.get("target_statute_id") or "").startswith("eu/"))
    fi_cites = len(ag.citations) - eu_cites
    print(f"\nCitations: {len(ag.citations)} total  "
          f"({fi_cites} FI→FI, {eu_cites} FI→EU)")
    # Top 10 most cited
    top = Counter(c.get("target_statute_id") for c in ag.citations
                  if not (c.get("target_statute_id") or "").startswith("eu/"))
    print("Top 10 most cited statutes:")
    for sid, cnt in top.most_common(10):
        title = ag.statute_meta.get(sid, {}).get("title", "")[:50]
        print(f"  {cnt:>5}  {sid}  {title}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: "argparse.Namespace") -> None:
    graph_dir = Path(args.graph)
    ag = load_artifact(graph_dir)

    if getattr(args, "stats", False):
        _print_stats(ag)
        return

    if getattr(args, "corpus_stats", False):
        _print_corpus_stats(ag)
        return

    sid = getattr(args, "statute_id", None)

    if getattr(args, "reverse_cites", False):
        if not sid:
            print("ERROR: --reverse-cites requires statute_id", file=sys.stderr)
            sys.exit(1)
        edges = ag.reverse_citations(sid)
        cites = [e for e in edges if e.get("edge_type") == "CITES"]
        other = [e for e in edges if e.get("edge_type") != "CITES"]
        print(f"CITES → {sid}: {len(cites)} edge(s)")
        for e in sorted(cites, key=lambda e: (e.get("source_statute_id", ""), e.get("source_section", ""))):
            src = f"§{e['source_section']} " if e.get("source_section") else ""
            tgt = f"#{e['target_section']}" if e.get("target_section") else ""
            cnt = f" x{e['count']}" if e.get("count", 1) > 1 else ""
            print(f"  {e['source_statute_id']} {src}→ {sid}{tgt}{cnt}")
        if other:
            print(f"\nOther edges targeting {sid}:")
            for e in other:
                print(f"  {e['source_statute_id']}: {e['edge_type']}")

    elif getattr(args, "delegates", False):
        if not sid:
            print("ERROR: --delegates requires statute_id", file=sys.stderr)
            sys.exit(1)
        section = getattr(args, "section", "") or ""
        edges = ag.delegations_for(sid, section)
        print(f"Delegation clauses in {sid}: {len(edges)}")
        for e in sorted(edges, key=lambda e: e.get("section", "")):
            print(f"  §{e.get('section', '?')}  [{e.get('delegation_type', '?')}]  eid={e.get('eid', '')}")

    elif getattr(args, "affecting_acts", False):
        if not sid:
            print("ERROR: --affecting-acts requires statute_id", file=sys.stderr)
            sys.exit(1)
        acts = ag.affecting_acts(sid)
        print(f"Acts amending {sid}: {len(acts)}")
        for a in sorted(acts):
            print(f"  {a}")

    elif getattr(args, "source_act", None):
        act_id = args.source_act
        targets = ag.targets_of_act(act_id)
        print(f"Statutes amended by {act_id}: {len(targets)}")
        for t in targets:
            title = ag.statute_meta.get(t, {}).get("title", "")[:60]
            print(f"  {t}  {title}")

    elif getattr(args, "issued_under", False):
        if not sid:
            print("ERROR: --issued-under requires statute_id", file=sys.stderr)
            sys.exit(1)
        decrees = ag.issued_under(sid)
        print(f"Decrees (asetukset) issued under {sid}: {len(decrees)}")
        for d in sorted(decrees, key=lambda d: (d.get("target_section", ""), d.get("source_statute_id", ""))):
            src = d.get("source_statute_id", "")
            title = ag.statute_meta.get(src, {}).get("title", "")[:58]
            auth_sec = f"§{d['target_section']}" if d.get("target_section") else "§?"
            print(f"  {auth_sec:6s}  {src}  {title}")

    elif getattr(args, "silent_breakage", False):
        if not sid:
            print("ERROR: --silent-breakage requires statute_id", file=sys.stderr)
            sys.exit(1)
        target_section = getattr(args, "provision", "") or ""
        results = ag.silent_breakage(sid, target_section)
        filter_note = f" (targeting §{target_section})" if target_section else ""
        print(f"Silent breakage — provisions citing {sid}{filter_note}: {len(results)}")
        for r in sorted(results, key=lambda r: (r.get("citing_statute", ""), r.get("citing_section", ""))):
            src = f"§{r['citing_section']} " if r.get("citing_section") else ""
            tgt = f"#{r['target_section']}" if r.get("target_section") else ""
            cnt = f" x{r['count']}" if r.get("count", 1) > 1 else ""
            print(f"  {r['citing_statute']} {src}→ {sid}{tgt}{cnt}")

    elif getattr(args, "breakage_report", None):
        changed = args.breakage_report
        results = ag.breakage_report(changed)
        print(f"Breakage report — {len(changed)} changed statute(s) → {len(results)} affected citation(s)")
        by_changed: dict = {}
        for r in results:
            by_changed.setdefault(r["changed_statute"], []).append(r)
        for changed_sid in changed:
            rows = by_changed.get(changed_sid, [])
            print(f"\n  {changed_sid} changed → {len(rows)} citing provision(s) potentially affected:")
            for r in sorted(rows, key=lambda r: (r.get("citing_statute", ""), r.get("citing_section", ""))):
                src = f"§{r['citing_section']} " if r.get("citing_section") else ""
                tgt = f"#{r['target_section']}" if r.get("target_section") else ""
                cnt = f" x{r['count']}" if r.get("count", 1) > 1 else ""
                print(f"    {r['citing_statute']} {src}→ {changed_sid}{tgt}{cnt}")

    elif getattr(args, "missing_decrees", False):
        # --missing-decrees SID [SID ...] — accepts one or more statute IDs
        target_sids = getattr(args, "statute_ids", None) or ([sid] if sid else [])
        if not target_sids:
            print("ERROR: --missing-decrees requires one or more statute_id(s)", file=sys.stderr)
            sys.exit(1)
        rows = ag.missing_decrees(target_sids)
        print(f"Mandatory delegation clauses with no decree ({len(rows)} gap(s) across {len(target_sids)} statute(s)):")
        for r in sorted(rows, key=lambda r: (r["statute_id"], r["section"])):
            text = r["match_text"][:80]
            print(f"  {r['statute_id']} §{r['section']}  {text}")

    elif getattr(args, "repeal_cascade", False):
        if not sid:
            print("ERROR: --repeal-cascade requires statute_id", file=sys.stderr)
            sys.exit(1)
        cascade = ag.repeal_cascade(sid)
        title = ag.statute_meta.get(sid, {}).get("title", "")
        alive = cascade["alive"]
        dead = cascade["already_repealed"]
        print(f"Repeal cascade for {sid}  {title}")
        print(f"  {len(cascade['all_decrees'])} decree(s) issued under this law")
        print(f"  {len(alive)} currently alive (would become orphaned)")
        print(f"  {len(dead)} already repealed in graph")
        if alive:
            print("\n  Alive decrees (orphaned if law is repealed):")
            for d in sorted(alive, key=lambda d: d.get("source_statute_id", "")):
                src = d.get("source_statute_id", "")
                dtitle = ag.statute_meta.get(src, {}).get("title", "")[:60]
                print(f"    {src}  {dtitle}")
        if dead:
            print("\n  Already repealed decrees (no additional impact):")
            for d in sorted(dead, key=lambda d: d.get("source_statute_id", "")):
                src = d.get("source_statute_id", "")
                print(f"    {src}")

    elif getattr(args, "delegation_chain", False):
        if not sid:
            print("ERROR: --delegation-chain requires statute_id", file=sys.stderr)
            sys.exit(1)
        show_unexercised = getattr(args, "show_unexercised", False)
        chain = ag.delegation_chain(sid)
        clauses = chain["clauses"]
        decrees = chain["decrees"]
        unexercised = chain["unexercised"]
        title = ag.statute_meta.get(sid, {}).get("title", "")
        print(f"Delegation chain for {sid}  {title}")
        if show_unexercised:
            print(f"  {len(unexercised)} unexercised clause(s) of {len(clauses)} total  "
                  f"{len(decrees)} decree(s) issued under")
            if unexercised:
                print("\n  Unexercised delegation clauses (no matching decree found):")
                for c in sorted(unexercised, key=lambda c: c.get("section", "")):
                    sec = f"§{c.get('section', '?')}"
                    dtype = c.get("delegation_type", "?")
                    text = c.get("match_text", "")[:80]
                    print(f"    {sec:8s} [{dtype}]  {text}")
            else:
                print("  (all delegation clauses appear to be exercised)")
            return
        print(f"  {len(clauses)} delegation clause(s)  {len(decrees)} decree(s) issued under")
        if clauses:
            print("\n  Delegation clauses:")
            for c in sorted(clauses, key=lambda c: c.get("section", "")):
                sec = f"§{c.get('section', '?')}"
                dtype = c.get("delegation_type", "?")
                text = c.get("match_text", "")[:80]
                print(f"    {sec:8s} [{dtype}]  {text}")
        if decrees:
            print("\n  Decrees (asetukset) issued under this law:")
            for d in sorted(decrees, key=lambda d: (d.get("target_section", ""), d.get("source_statute_id", ""))):
                src = d.get("source_statute_id", "")
                dtitle = ag.statute_meta.get(src, {}).get("title", "")[:55]
                auth_sec = f"§{d['target_section']}" if d.get("target_section") else "§?"
                print(f"    {auth_sec:6s}  {src}  {dtitle}")
        if not clauses and not decrees:
            print("  (no delegation clauses or decrees found in artifact)")
        elif clauses and not decrees:
            print("\n  NOTE: no decrees found — delegation(s) may be unexercised")

    elif getattr(args, "eu_refs", False):
        if not sid:
            print("ERROR: --eu-refs requires statute_id", file=sys.stderr)
            sys.exit(1)
        edges = ag.eu_refs(sid)
        print(f"EU references from {sid}: {len(edges)}")
        for e in sorted(edges, key=lambda e: (e.get("target_statute_id", ""), e.get("source_section", ""))):
            src_sec = f"§{e['source_section']}" if e.get("source_section") else ""
            tgt = e.get("target_statute_id", "")
            tgt_sec = f" art.{e['target_section']}" if e.get("target_section") else ""
            print(f"  {src_sec:8s} → {tgt}{tgt_sec}")

    elif sid:
        # Show statute info
        meta = ag.statute_meta.get(sid)
        if not meta:
            print(f"Statute {sid!r} not in artifact.", file=sys.stderr)
            sys.exit(1)
        print(f"{sid}")
        print(f"  title:  {meta.get('title', '')}")
        print(f"  type:   {meta.get('statute_type', '')}")
        amends = ag.affecting_acts(sid)
        print(f"  amended by: {len(amends)} act(s)")
        cites_out = [c for c in ag.citations if c.get("source_statute_id") == sid]
        cites_in = ag.reverse_citations(sid)
        eu_out = ag.eu_refs(sid)
        print(f"  outgoing cites: {len(cites_out)}  (EU: {len(eu_out)})")
        print(f"  incoming cites: {len(cites_in)}")
    else:
        _print_meta(ag)


def register_cli(sub: Any) -> None:
    """Register the 'query' subcommand onto an argparse subparsers object."""
    query_p = sub.add_parser(
        "query",
        help="run graph queries against a pre-built artifact (faster than lawvm graph)",
        description=(
            "Read from a directory produced by `lawvm build` and run cross-statute queries."
        ),
    )
    query_p.add_argument(
        "--graph", metavar="DIR", required=True,
        help="artifact directory (produced by lawvm build)",
    )
    query_p.add_argument(
        "statute_id", nargs="?",
        help="statute ID to query (e.g. 2009/953)",
    )
    query_p.add_argument(
        "--reverse-cites", action="store_true",
        help="show statutes that cite statute_id",
    )
    query_p.add_argument(
        "--affecting-acts", action="store_true",
        help="show acts that have amended statute_id",
    )
    query_p.add_argument(
        "--delegates", action="store_true",
        help="show delegation clauses in statute_id",
    )
    query_p.add_argument(
        "--silent-breakage", action="store_true",
        help="show provisions that cite statute_id (may have been silently affected)",
    )
    query_p.add_argument(
        "--provision", metavar="FRAG",
        help="filter --silent-breakage to provisions citing this section fragment",
    )
    query_p.add_argument(
        "--section", metavar="SEC",
        help="filter --delegates to this source section",
    )
    query_p.add_argument(
        "--stats", action="store_true",
        help="show artifact stats and top-cited statutes",
    )
    query_p.add_argument(
        "--breakage-report", metavar="SID", nargs="+",
        dest="breakage_report",
        help="push-based: given a list of changed statute IDs, report all citation edges "
             "that may have been silently invalidated (e.g. after a batch amendment update)",
    )
    query_p.add_argument(
        "--source-act", metavar="ACT_ID",
        dest="source_act",
        help="which statutes does amendment act ACT_ID affect? (e.g. 1999/623)",
    )
    query_p.add_argument(
        "--issued-under", action="store_true",
        dest="issued_under",
        help="show decrees (asetukset) issued under statute_id (ISSUED_UNDER edges)",
    )
    query_p.add_argument(
        "--eu-refs", action="store_true",
        dest="eu_refs",
        help="show EU cross-reference edges sourced from statute_id (FI→EU)",
    )
    query_p.add_argument(
        "--delegation-chain", action="store_true",
        dest="delegation_chain",
        help="show delegation clauses + decrees issued under statute_id (combined view)",
    )
    query_p.add_argument(
        "--show-unexercised", action="store_true",
        dest="show_unexercised",
        help="with --delegation-chain: filter to delegation clauses with no matching decree (requires Phase 8.4 section data)",
    )
    query_p.add_argument(
        "--repeal-cascade", action="store_true",
        dest="repeal_cascade",
        help="show decrees that become orphaned if statute_id is repealed (ISSUED_UNDER impact analysis)",
    )
    query_p.add_argument(
        "--missing-decrees", action="store_true",
        dest="missing_decrees",
        help="show mandatory delegation clauses in statute_id(s) that have no ISSUED_UNDER decree",
    )
    query_p.add_argument(
        "--corpus-stats", action="store_true",
        dest="corpus_stats",
        help="print full corpus breakdown by decade × statute type with amendment distribution",
    )
