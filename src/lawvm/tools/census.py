"""lawvm census — Tier 1 corpus-level census queries against a build artifact.

Census 1.1: Stale Reference Census     — citations to since-amended statutes
Census 1.2: Delegation Gap Census      — unexercised delegation clauses
Census 1.3: Orphaned Decree Census     — decrees citing repealed authority
Census 1.4: Complexity Trajectory      — volume / density by decade
Census 1.5: EU Reference Inventory     — FI→EU citation map

Usage:
    lawvm census --graph .tmp/corpus_graph/ --output .tmp/census_results/
    lawvm census --graph .tmp/corpus_graph/ --output .tmp/census_results/ --report
    lawvm census --graph .tmp/corpus_graph/ --output .tmp/census_results/ --only 1.1,1.3
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

_MANDATORY_RE = re.compile(r'\b(säädetään|on annettava|on säädettävä)\b', re.I)


# ---------------------------------------------------------------------------
# Census 1.1: Stale Reference Census
# ---------------------------------------------------------------------------

def census_1_1(citations: list, amendment_index: dict) -> tuple[list, dict]:
    """CITES edges where target was amended after citing statute enacted."""
    latest_amend_year: dict[str, int] = {}
    for parent_id, amend_list in amendment_index.items():
        years = [int(a.split('/')[0]) for a in amend_list if a.split('/')[0].isdigit()]
        if years:
            latest_amend_year[parent_id] = max(years)

    rows = []
    total_fi = 0
    for cite in citations:
        if cite.get('edge_type') != 'CITES':
            continue
        src = cite.get('source_statute_id', '')
        tgt = cite.get('target_statute_id', '')
        if not src or not tgt or tgt.startswith('eu/'):
            continue
        total_fi += 1
        try:
            citing_year = int(src.split('/')[0])
        except ValueError:
            continue
        tgt_last = latest_amend_year.get(tgt)
        if tgt_last and citing_year < tgt_last:
            rows.append({
                'citing_statute': src,
                'citing_section': cite.get('source_section', ''),
                'target_statute': tgt,
                'target_section': cite.get('target_section', ''),
                'citation_enacted': citing_year,
                'target_last_amended': tgt_last,
                'staleness_years': tgt_last - citing_year,
            })

    rows.sort(key=lambda r: -r['staleness_years'])
    stats = {
        'stale': len(rows),
        'total_fi_cites': total_fi,
        'pct': len(rows) / total_fi * 100 if total_fi > 0 else 0.0,
        'long_stale': sum(1 for r in rows if r['staleness_years'] >= 20),
    }
    return rows, stats


# ---------------------------------------------------------------------------
# Census 1.2: Delegation Gap Census
# ---------------------------------------------------------------------------

def census_1_2(citations: list, delegations: list) -> tuple[list, dict]:
    """Unexercised delegation clauses — no ISSUED_UNDER child decree found."""
    has_child: set[str] = {
        c.get('target_statute_id', '')
        for c in citations
        if c.get('edge_type') == 'ISSUED_UNDER' and c.get('target_statute_id')
    }

    deleg_total: dict[str, int] = defaultdict(int)
    deleg_mandatory: dict[str, int] = defaultdict(int)
    # first mandatory clause verbatim text and trigger word per statute
    verbatim: dict[str, str] = {}
    trigger: dict[str, str] = {}
    for d in delegations:
        sid = d.get('statute_id', '')
        if not sid:
            continue
        deleg_total[sid] += 1
        text = d.get('match_text', '')
        m = _MANDATORY_RE.search(text)
        if m:
            deleg_mandatory[sid] += 1
            if sid not in verbatim:
                verbatim[sid] = d.get('quote', '') or text
                trigger[sid] = m.group(1)

    rows = []
    for sid, total in sorted(deleg_total.items(), key=lambda x: -x[1]):
        exercised = sid in has_child
        rows.append({
            'statute_id': sid,
            'delegation_clauses': total,
            'mandatory_clauses': deleg_mandatory.get(sid, 0),
            'has_child_decree': exercised,
            'unexercised_clauses': 0 if exercised else total,
            'mandatory_unexercised': 0 if exercised else deleg_mandatory.get(sid, 0),
            'verbatim_text': verbatim.get(sid, '') if not exercised else '',
            'trigger_word': trigger.get(sid, '') if not exercised else '',
        })

    total_clauses = sum(r['delegation_clauses'] for r in rows)
    unexercised_total = sum(r['unexercised_clauses'] for r in rows)
    stats = {
        'total_clauses': total_clauses,
        'total_statutes': len(rows),
        'statutes_with_child': len(has_child),
        'unexercised_clauses': unexercised_total,
        'unexercised_pct': unexercised_total / total_clauses * 100 if total_clauses > 0 else 0.0,
        'mandatory_unexercised': sum(r['mandatory_unexercised'] for r in rows),
    }
    # Return only rows with unexercised clauses
    return [r for r in rows if r['unexercised_clauses'] > 0], stats


# ---------------------------------------------------------------------------
# Census 1.3: Orphaned Decree Census
# ---------------------------------------------------------------------------

def census_1_3(citations: list) -> tuple[list, dict]:
    """Decrees with ISSUED_UNDER link to a statute that was subsequently REPEALED."""
    repealed: set[str] = {
        c.get('target_statute_id', '')
        for c in citations
        if c.get('edge_type') == 'REPEALS' and c.get('target_statute_id')
    }

    rows = []
    seen: set = set()
    total_issued_under = 0
    for cite in citations:
        if cite.get('edge_type') != 'ISSUED_UNDER':
            continue
        total_issued_under += 1
        decree_id = cite.get('source_statute_id', '')
        parent_id = cite.get('target_statute_id', '')
        if not decree_id or not parent_id:
            continue
        if parent_id in repealed:
            key = (decree_id, parent_id)
            if key not in seen:
                seen.add(key)
                try:
                    decree_year = int(decree_id.split('/')[0])
                except ValueError:
                    decree_year = 0
                try:
                    parent_year = int(parent_id.split('/')[0])
                except ValueError:
                    parent_year = 0
                rows.append({
                    'decree_id': decree_id,
                    'decree_year': decree_year,
                    'authority_statute': parent_id,
                    'authority_year': parent_year,
                })

    rows.sort(key=lambda r: r['authority_statute'])
    stats = {
        'orphaned': len(rows),
        'total_issued_under': total_issued_under,
        'total_repealed_statutes': len(repealed),
        'pct': len(rows) / total_issued_under * 100 if total_issued_under > 0 else 0.0,
    }
    return rows, stats


# ---------------------------------------------------------------------------
# Census 1.4: Complexity Trajectory
# ---------------------------------------------------------------------------

def census_1_4(statute_meta: dict, citations: list, delegations: list,
               amendment_index: dict) -> tuple[list, dict]:
    """Statute volume, citation density, delegation density, amendment rate — by decade."""
    by_decade: dict = defaultdict(lambda: {
        'statutes': 0, 'fi_cites': 0, 'eu_cites': 0,
        'delegations': 0, 'amendments': 0,
    })

    for sid in statute_meta:
        try:
            decade = (int(sid.split('/')[0]) // 10) * 10
            by_decade[decade]['statutes'] += 1
        except (ValueError, IndexError):
            pass

    for cite in citations:
        if cite.get('edge_type') != 'CITES':
            continue
        src = cite.get('source_statute_id', '')
        try:
            decade = (int(src.split('/')[0]) // 10) * 10
            if (cite.get('target_statute_id') or '').startswith('eu/'):
                by_decade[decade]['eu_cites'] += 1
            else:
                by_decade[decade]['fi_cites'] += 1
        except (ValueError, IndexError):
            pass

    for d in delegations:
        sid = d.get('statute_id', '')
        try:
            decade = (int(sid.split('/')[0]) // 10) * 10
            by_decade[decade]['delegations'] += 1
        except (ValueError, IndexError):
            pass

    for parent_id, amend_list in amendment_index.items():
        try:
            decade = (int(parent_id.split('/')[0]) // 10) * 10
            by_decade[decade]['amendments'] += len(amend_list)
        except (ValueError, IndexError):
            pass

    rows = []
    for decade in sorted(by_decade):
        d = by_decade[decade]
        n = max(d['statutes'], 1)
        rows.append({
            'decade': decade,
            'statutes': d['statutes'],
            'fi_cites': d['fi_cites'],
            'eu_cites': d['eu_cites'],
            'delegations': d['delegations'],
            'amendments': d['amendments'],
            'cite_density': round(d['fi_cites'] / n, 3),
            'deleg_density': round(d['delegations'] / n, 3),
            'amend_per_statute': round(d['amendments'] / n, 3),
        })
    return rows, {}


# ---------------------------------------------------------------------------
# Census 1.5: EU Reference Inventory
# ---------------------------------------------------------------------------

def census_1_5(citations: list) -> tuple[list, dict]:
    """FI→EU CITES edges: which EU acts are cited and how many Finnish statutes cite them."""
    eu_cites = [
        c for c in citations
        if c.get('edge_type') == 'CITES' and
        (c.get('target_statute_id') or '').startswith('eu/')
    ]

    target_count: Counter = Counter(c.get('target_statute_id') for c in eu_cites)
    citing_by_tgt: dict[str, set] = defaultdict(set)
    for c in eu_cites:
        citing_by_tgt[c.get('target_statute_id', '')].add(c.get('source_statute_id', ''))

    rows = []
    for tgt, cnt in target_count.most_common():
        parts = tgt.split('/')
        rows.append({
            'eu_act_id': tgt,
            'eu_type': parts[1] if len(parts) >= 2 else '',
            'eu_year': parts[2] if len(parts) >= 3 else '',
            'eu_number': parts[3] if len(parts) >= 4 else '',
            'total_citations': cnt,
            'fi_statutes_citing': len(citing_by_tgt[tgt]),
        })

    by_type = Counter(r['eu_type'] for r in rows)
    stats = {
        'total_edges': len(eu_cites),
        'unique_eu_acts': len(target_count),
        'fi_statutes': len({c.get('source_statute_id') for c in eu_cites}),
        'by_type': dict(by_type),
    }
    return rows, stats


# ---------------------------------------------------------------------------
# CSV helper
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Markdown report generator
# ---------------------------------------------------------------------------

def _generate_report(results: dict, corpus_size: int) -> str:
    s11 = results.get('1.1', ({}, {}))[1]
    s12 = results.get('1.2', ({}, {}))[1]
    s13 = results.get('1.3', ({}, {}))[1]
    rows14 = results.get('1.4', ([], {}))[0]
    s15 = results.get('1.5', ({}, {}))[1]

    def _pct(n: int, d: int) -> str:
        return f"{n / d * 100:.1f}%" if d else "N/A"

    # Trajectory note
    traj_note = ''
    valid = [r for r in rows14 if r.get('statutes', 0) >= 100]
    if valid:
        lo = min(valid, key=lambda r: r['cite_density'])
        hi = max(valid, key=lambda r: r['cite_density'])
        traj_note = f"{lo['cite_density']:.1f} cites/statute ({lo['decade']}s) → {hi['cite_density']:.1f} ({hi['decade']}s)"

    eu_type_str = ', '.join(
        f"{k}={v:,}" for k, v in sorted(s15.get('by_type', {}).items(), key=lambda x: -x[1])
    )

    # Trajectory table rows
    traj_table = ''
    for r in rows14:
        if r.get('statutes', 0) == 0:
            continue
        traj_table += (
            f"| {r['decade']}s | {r['statutes']:,} | {r['fi_cites']:,} | {r['eu_cites']:,} | "
            f"{r['delegations']:,} | {r['amend_per_statute']:.1f} | {r['cite_density']:.2f} |\n"
        )

    return f"""\
# Oikeusjärjestelmän infrastruktuurikartoitus 2026
## Finnish Legal Infrastructure Census Report

> Generated by `lawvm census` from {corpus_size:,} statutes.
> Source: Finlex Akoma Ntoso consolidated statute corpus.

---

## Tiivistelmä / Executive Summary

| Census | Finding |
|--------|---------|
| 1.1 Stale References | **{s11.get('stale', 0):,}/{s11.get('total_fi_cites', 0):,} ({s11.get('pct', 0):.1f}%)** FI→FI citations stale; {s11.get('long_stale', 0):,} with 20+ year gap |
| 1.2 Delegation Gaps | **{s12.get('unexercised_clauses', 0):,}/{s12.get('total_clauses', 0):,} ({s12.get('unexercised_pct', 0):.1f}%)** clauses unexercised; {s12.get('mandatory_unexercised', 0)} mandatory |
| 1.3 Orphaned Decrees | **{s13.get('orphaned', 0):,}** decrees cite repealed authority ({_pct(s13.get('orphaned',0), s13.get('total_issued_under',1))} of all ISSUED_UNDER links) |
| 1.4 Trajectory | Citation density: {traj_note} |
| 1.5 EU References | **{s15.get('total_edges', 0):,}** FI→EU edges across {s15.get('fi_statutes', 0):,} statutes; {s15.get('unique_eu_acts', 0):,} unique EU acts |

---

## Census 1.1: Stale Reference Census

**Headline:** {s11.get('stale', 0):,} out of {s11.get('total_fi_cites', 0):,} Finnish statutory \
cross-references ({s11.get('pct', 0):.1f}%) cite a statute that was subsequently amended.
No notification mechanism exists. Of these, {s11.get('long_stale', 0):,} have a staleness gap of 20+ years.

**Finnish:** "Suomen oikeusjärjestelmässä on {s11.get('stale', 0):,} viittausta lakeihin jotka ovat \
muuttuneet viittauksen jälkeen. Kukaan ei tarkista näitä."

---

## Census 1.2: Delegation Gap Census

**Headline:** {s12.get('unexercised_clauses', 0):,} out of {s12.get('total_clauses', 0):,} \
delegation clauses ({s12.get('unexercised_pct', 0):.1f}%) have no corresponding decree.
Parliament authorized regulation that was never written.

Of these, {s12.get('mandatory_unexercised', 0)} clauses used mandatory language \
("säädetään" / "on annettava") — a legal obligation to issue a decree that was not fulfilled.

**Finnish:** "Eduskunta on valtuuttanut {s12.get('unexercised_clauses', 0):,} asetuksen \
antamisen, joita ei ole koskaan annettu."

---

## Census 1.3: Orphaned Decree Census

**Headline:** {s13.get('orphaned', 0):,} decrees remain in formal operation while citing \
authorization from statutes that have since been repealed. These decrees may be ultra vires.

Total ISSUED_UNDER linkages examined: {s13.get('total_issued_under', 0):,}.

**Finnish:** "{s13.get('orphaned', 0):,} voimassaolevaa asetusta viittaa valtuutussäännökseen \
joka on kumottu."

---

## Census 1.4: Regulatory Complexity Trajectory

| Decade | Statutes | FI→FI Cites | FI→EU Cites | Delegations | Amend/Statute | Cite Density |
|--------|----------|-------------|-------------|-------------|---------------|-------------|
{traj_table}
---

## Census 1.5: EU Reference Inventory

**Headline:** {s15.get('total_edges', 0):,} FI→EU citation edges across {s15.get('fi_statutes', 0):,} \
Finnish statutes, referencing {s15.get('unique_eu_acts', 0):,} unique EU acts.

By type: {eu_type_str}

These references are structural dependencies: if an EU act is amended, Finnish implementing
law is silently affected — with no automatic notification mechanism.

---

*Methodology: see FINDINGS_ROADMAP.md. All figures from the Finlex consolidated corpus.*
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: "argparse.Namespace") -> None:
    from lawvm.tools.query import load_artifact

    graph_dir = Path(args.graph)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    only = set(args.only.split(',')) if getattr(args, 'only', None) else {'1.1', '1.2', '1.3', '1.4', '1.5'}

    print(f"Loading artifact from {graph_dir}...")
    ag = load_artifact(graph_dir)
    corpus_size = ag.meta.get('corpus_size', len(ag.statute_meta))
    print(f"  {corpus_size:,} statutes | {len(ag.citations):,} citation edges | {len(ag.delegations):,} delegation clauses")

    results: dict[str, tuple[list, dict]] = {}

    if '1.1' in only:
        print("\nCensus 1.1: Stale Reference Census...")
        rows, stats = census_1_1(ag.citations, ag.amendment_index)
        _write_csv(output_dir / 'census_1_1_stale_references.csv', rows)
        print(f"  {stats['stale']:,}/{stats['total_fi_cites']:,} stale ({stats['pct']:.1f}%)"
              f" | {stats['long_stale']:,} with 20+ year gap")
        results['1.1'] = (rows, stats)

    if '1.2' in only:
        print("\nCensus 1.2: Delegation Gap Census...")
        rows, stats = census_1_2(ag.citations, ag.delegations)
        _write_csv(output_dir / 'census_1_2_delegation_gaps.csv', rows)
        print(f"  {stats['unexercised_clauses']:,}/{stats['total_clauses']:,} unexercised"
              f" ({stats['unexercised_pct']:.1f}%) | {stats['mandatory_unexercised']} mandatory")
        results['1.2'] = (rows, stats)

    if '1.3' in only:
        print("\nCensus 1.3: Orphaned Decree Census...")
        rows, stats = census_1_3(ag.citations)
        _write_csv(output_dir / 'census_1_3_orphaned_decrees.csv', rows)
        print(f"  {stats['orphaned']:,} orphaned of {stats['total_issued_under']:,}"
              f" ISSUED_UNDER links ({stats['pct']:.1f}%)")
        results['1.3'] = (rows, stats)

    if '1.4' in only:
        print("\nCensus 1.4: Complexity Trajectory...")
        rows, stats = census_1_4(ag.statute_meta, ag.citations, ag.delegations, ag.amendment_index)
        _write_csv(output_dir / 'census_1_4_complexity_trajectory.csv', rows)
        for r in rows:
            if r['statutes'] > 0:
                print(f"  {r['decade']}s: {r['statutes']:>6,} statutes "
                      f"cite_density={r['cite_density']:.2f} "
                      f"deleg={r['deleg_density']:.2f} "
                      f"amend/statute={r['amend_per_statute']:.1f}")
        results['1.4'] = (rows, stats)

    if '1.5' in only:
        print("\nCensus 1.5: EU Reference Inventory...")
        rows, stats = census_1_5(ag.citations)
        _write_csv(output_dir / 'census_1_5_eu_refs.csv', rows)
        by_type_str = ', '.join(f"{k}={v}" for k, v in sorted(stats['by_type'].items(), key=lambda x: -x[1]))
        print(f"  {stats['total_edges']:,} FI→EU edges | {stats['unique_eu_acts']:,} unique EU acts"
              f" | {stats['fi_statutes']:,} FI statutes")
        print(f"  By type: {by_type_str}")
        results['1.5'] = (rows, stats)

    if getattr(args, 'report', False):
        # Ensure 1.4 rows are available for the table
        if '1.4' not in results:
            rows14, _ = census_1_4(ag.statute_meta, ag.citations, ag.delegations, ag.amendment_index)
            results['1.4'] = (rows14, {})
        print("\nGenerating Markdown report...")
        md = _generate_report(results, corpus_size)
        report_path = output_dir / 'census_report.md'
        report_path.write_text(md, encoding='utf-8')
        print(f"  Wrote {report_path}")

    print(f"\nOutputs written to {output_dir}/")
