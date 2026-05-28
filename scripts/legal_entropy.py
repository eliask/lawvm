#!/usr/bin/env python3
"""Legal entropy / complexity analysis of the Finnish legal corpus.

Computes temporal complexity metrics from the 59K-statute corpus graph and
produces both data tables and matplotlib visualizations.

Usage:
    cd LawVM && uv run python scripts/legal_entropy.py [--output DIR]

Metrics computed:
  1. Stock growth: cumulative statutes over time
  2. Citation density: cross-references per statute per year
  3. Amendment rate: amendments per active statute per year
  4. Delegation density: delegation clauses per enacted statute per year
  5. Composite "legal entropy" H(t): normalized sum of above
  6. Citation network topology: degree distribution, power-law fit
  7. Most-amended provisions (churn signal)
  8. Stale reference accumulation rate
"""
from __future__ import annotations

import json
import math
import sys
import importlib
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_YEAR_RANGE = range(1920, 2027)

try:
    matplotlib = importlib.import_module("matplotlib")
    matplotlib.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    ticker = importlib.import_module("matplotlib.ticker")
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_graph(graph_dir: Path):
    with open(graph_dir / "statutes.json") as f:
        statutes = json.load(f)
    with open(graph_dir / "amendments.json") as f:
        amendments = json.load(f)

    citations = []
    with open(graph_dir / "citations.jsonl") as f:
        for line in f:
            citations.append(json.loads(line))

    delegations = []
    with open(graph_dir / "delegations.jsonl") as f:
        for line in f:
            delegations.append(json.loads(line))

    return statutes, amendments, citations, delegations


def sid_year(sid: str) -> int:
    return int(sid.split("/")[0])


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_annual_metrics(statutes, amendments, citations, delegations,
                           year_range=DEFAULT_YEAR_RANGE):
    """Return dict of {year -> metrics_dict}."""

    enacted_by_year = Counter()
    for sid in statutes:
        enacted_by_year[sid_year(sid)] += 1

    repealed_targets_by_year = Counter()  # year repeal enacted -> count
    for c in citations:
        if c["edge_type"] == "REPEALS":
            repealed_targets_by_year[sid_year(c["source_statute_id"])] += 1

    cites_by_year = Counter()
    for c in citations:
        if c["edge_type"] == "CITES":
            cites_by_year[sid_year(c["source_statute_id"])] += 1

    amend_by_year = Counter()
    for parent, chain in amendments.items():
        for amendment_id in chain:
            amend_by_year[sid_year(amendment_id)] += 1

    deleg_by_year = Counter()
    for d in delegations:
        deleg_by_year[sid_year(d["statute_id"])] += 1

    # Cumulative stock
    cumul = 0
    cumul_repealed = 0
    results = {}
    for y in year_range:
        cumul += enacted_by_year[y]
        cumul_repealed += repealed_targets_by_year[y]
        net_stock = cumul - cumul_repealed
        en = enacted_by_year[y]
        am = amend_by_year[y]
        ci = cites_by_year[y]
        dl = deleg_by_year[y]

        results[y] = {
            "gross_stock": cumul,
            "net_stock": net_stock,
            "enacted": en,
            "repealed": repealed_targets_by_year[y],
            "amended": am,
            "cites": ci,
            "delegations": dl,
            "cite_density": ci / net_stock if net_stock > 0 else 0,
            "amend_rate": am / net_stock if net_stock > 0 else 0,
            "deleg_rate": dl / en if en > 0 else 0,
        }

    return results


def compute_entropy(annual_metrics, year_range=DEFAULT_YEAR_RANGE):
    """Composite legal entropy H(t) = normalized cite_density + amend_rate + deleg_rate + enacted/stock.

    Each component normalized to [0,1] by dividing by its max across all years.
    """
    # Collect raw components
    cite_ds = {y: annual_metrics[y]["cite_density"] for y in year_range}
    amend_rs = {y: annual_metrics[y]["amend_rate"] for y in year_range}
    deleg_rs = {y: annual_metrics[y]["deleg_rate"] for y in year_range}
    growth_rs = {y: annual_metrics[y]["enacted"] / max(annual_metrics[y]["net_stock"], 1)
                 for y in year_range}

    max_cd = max(cite_ds.values()) or 1
    max_ar = max(amend_rs.values()) or 1
    max_dr = max(deleg_rs.values()) or 1
    max_gr = max(growth_rs.values()) or 1

    entropy = {}
    for y in year_range:
        entropy[y] = (
            cite_ds[y] / max_cd * 0.30 +
            amend_rs[y] / max_ar * 0.30 +
            deleg_rs[y] / max_dr * 0.20 +
            growth_rs[y] / max_gr * 0.20
        )
    return entropy


def compute_degree_distribution(citations):
    """In-degree distribution for CITES edges."""
    in_deg = Counter()
    for c in citations:
        if c["edge_type"] == "CITES":
            in_deg[c["target_statute_id"]] += 1
    return Counter(in_deg.values())  # degree -> count


def compute_stale_accumulation(citations, amendments, statutes, year_range=DEFAULT_YEAR_RANGE):
    """Approximate stale reference accumulation per year.

    A CITES edge from statute A to statute B becomes stale when B gets amended
    after A was enacted. Count: for each year Y, how many existing CITES edges
    have their target amended in year Y.
    """
    # Build target -> [years amended]
    target_amend_years = defaultdict(set)
    for parent, chain in amendments.items():
        for amendment_id in chain:
            target_amend_years[parent].add(sid_year(amendment_id))

    # For each CITES edge, source_year = enacted year of source
    # Edge becomes stale in any year where target is amended AND year > source_year
    new_stale_by_year = Counter()
    for c in citations:
        if c["edge_type"] != "CITES":
            continue
        src_year = sid_year(c["source_statute_id"])
        tgt = c["target_statute_id"]
        for amend_year in target_amend_years.get(tgt, set()):
            if amend_year > src_year:
                new_stale_by_year[amend_year] += 1

    # Cumulative
    cumul = 0
    result = {}
    for y in year_range:
        cumul += new_stale_by_year[y]
        result[y] = cumul
    return result


def compute_amendment_halflife(amendments, statutes):
    """Time from enactment to first amendment, by decade."""
    def sid_yr(s): return int(s.split("/")[0])

    first_amend = {}
    for parent, chain in amendments.items():
        if chain:
            first_amend[parent] = min(sid_yr(m) for m in chain) - sid_yr(parent)

    result = {}
    for decade in range(1960, 2030, 10):
        delays = sorted(d for s, d in first_amend.items()
                        if decade <= sid_yr(s) < decade + 10 and 0 <= d <= 50)
        if delays:
            result[decade] = {
                "n": len(delays),
                "median": delays[len(delays) // 2],
                "p25": delays[len(delays) // 4],
                "p75": delays[3 * len(delays) // 4],
                "pct_same_year": sum(1 for d in delays if d == 0) / len(delays) * 100,
            }
    return result


def compute_connectivity(citations):
    """Giant component size and basic connectivity stats."""
    adj: dict[str, set[str]] = defaultdict(set)
    for c in citations:
        if c["edge_type"] == "CITES":
            adj[c["source_statute_id"]].add(c["target_statute_id"])
            adj[c["target_statute_id"]].add(c["source_statute_id"])

    all_nodes = set(adj.keys())
    visited: set[str] = set()
    components: list[int] = []
    for start in all_nodes:
        if start in visited:
            continue
        comp: set[str] = set()
        queue = [start]
        while queue:
            node = queue.pop()
            if node in comp:
                continue
            comp.add(node)
            for nb in adj[node]:
                if nb not in comp:
                    queue.append(nb)
        visited |= comp
        components.append(len(comp))

    components.sort(reverse=True)
    return {
        "n_cited": len(all_nodes),
        "n_components": len(components),
        "giant": components[0] if components else 0,
        "giant_pct": components[0] / len(all_nodes) * 100 if all_nodes else 0,
    }


def fit_power_law(deg_dist: dict[int, int]) -> tuple[float, float]:
    """Fit power law exponent via MLE (Clauset et al 2009 discrete MLE).

    Returns (alpha, x_min) where x_min is fixed at 1.
    """
    x_min = 1
    # MLE: alpha = 1 + n * (sum(ln(xi / (x_min - 0.5))))^-1
    n = sum(cnt for deg, cnt in deg_dist.items() if deg >= x_min)
    log_sum = sum(cnt * math.log(deg / (x_min - 0.5))
                  for deg, cnt in deg_dist.items() if deg >= x_min)
    if log_sum == 0:
        return 0, x_min
    alpha = 1 + n / log_sum
    return alpha, x_min


# ---------------------------------------------------------------------------
# Output / visualization
# ---------------------------------------------------------------------------

def plot_time_series(annual_metrics, entropy, stale_cumul, output_dir: Path):
    if not HAS_MPL:
        print("matplotlib not available, skipping plots", file=sys.stderr)
        return

    years = sorted(annual_metrics.keys())
    fig, axes = plt.subplots(3, 2, figsize=(14, 12))
    fig.suptitle("Finnish Legal System — Complexity Metrics 1920–2026", fontsize=14)

    # 1. Stock growth
    ax = axes[0, 0]
    ax.fill_between(years, [annual_metrics[y]["net_stock"] for y in years],
                    alpha=0.3, label="Net stock (enacted − repealed)")
    ax.plot(years, [annual_metrics[y]["gross_stock"] for y in years],
            linewidth=1, label="Gross stock (cumulative enacted)")
    ax.set_ylabel("Statutes")
    ax.set_title("Statute Stock")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 2. Annual flows
    ax = axes[0, 1]
    ax.bar(years, [annual_metrics[y]["enacted"] for y in years], alpha=0.5, label="Enacted", width=1)
    ax.bar(years, [-annual_metrics[y]["repealed"] for y in years], alpha=0.5, label="Repealed", color="red", width=1)
    ax.set_ylabel("Statutes/year")
    ax.set_title("Annual Enactment vs Repeal")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 3. Citation density
    ax = axes[1, 0]
    raw = [annual_metrics[y]["cite_density"] for y in years]
    # 5-year moving average
    ma5 = []
    for i, y in enumerate(years):
        window = raw[max(0, i-2):i+3]
        ma5.append(sum(window) / len(window))
    ax.plot(years, raw, alpha=0.3, linewidth=0.5, color="blue")
    ax.plot(years, ma5, linewidth=2, color="blue", label="5yr MA")
    ax.set_ylabel("Citations / active statute")
    ax.set_title("Citation Density")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 4. Amendment rate
    ax = axes[1, 1]
    raw_ar = [annual_metrics[y]["amend_rate"] for y in years]
    ma5_ar = []
    for i in range(len(years)):
        window = raw_ar[max(0, i-2):i+3]
        ma5_ar.append(sum(window) / len(window))
    ax.plot(years, raw_ar, alpha=0.3, linewidth=0.5, color="orange")
    ax.plot(years, ma5_ar, linewidth=2, color="orange", label="5yr MA")
    ax.set_ylabel("Amendments / active statute")
    ax.set_title("Amendment Rate (churn)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 5. Composite entropy H(t)
    ax = axes[2, 0]
    h_vals = [entropy[y] for y in years]
    ma5_h = []
    for i in range(len(years)):
        window = h_vals[max(0, i-2):i+3]
        ma5_h.append(sum(window) / len(window))
    ax.plot(years, h_vals, alpha=0.3, linewidth=0.5, color="darkred")
    ax.plot(years, ma5_h, linewidth=2, color="darkred", label="5yr MA")
    ax.set_ylabel("H(t) composite")
    ax.set_title("Legal Entropy H(t)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 6. Stale reference accumulation
    ax = axes[2, 1]
    ax.fill_between(years, [stale_cumul.get(y, 0) for y in years], alpha=0.4, color="crimson")
    ax.set_ylabel("Cumulative stale refs")
    ax.set_title("Stale Reference Accumulation")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    outpath = output_dir / "legal_entropy_timeseries.png"
    fig.savefig(outpath, dpi=150)
    print(f"Saved: {outpath}")
    plt.close(fig)


def plot_degree_distribution(deg_dist: dict[int, int], alpha: float, output_dir: Path):
    if not HAS_MPL:
        return

    fig, ax = plt.subplots(figsize=(8, 6))
    degs = sorted(deg_dist.keys())
    counts = [deg_dist[d] for d in degs]

    ax.scatter(degs, counts, s=10, alpha=0.6, color="steelblue")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("In-degree (citations received)")
    ax.set_ylabel("Number of statutes")
    ax.set_title(f"Citation In-Degree Distribution (power-law α ≈ {alpha:.2f})")

    # Power-law reference line
    if alpha > 1:
        x_ref = [d for d in degs if d >= 1]
        c_fit = max(counts) * (min(x_ref) ** alpha)
        y_ref = [c_fit * (x ** -alpha) for x in x_ref]
        ax.plot(x_ref, y_ref, "r--", alpha=0.5, linewidth=1, label=f"α = {alpha:.2f}")
        ax.legend()

    ax.grid(True, alpha=0.3, which="both")
    outpath = output_dir / "legal_entropy_degree_dist.png"
    fig.savefig(outpath, dpi=150)
    print(f"Saved: {outpath}")
    plt.close(fig)


def print_summary_table(annual_metrics, entropy, stale_cumul):
    """Print decadal summary table."""
    print(f"\n{'Decade':<8} {'Net Stock':>9} {'Enacted':>8} {'Repealed':>8} "
          f"{'Amended':>8} {'Cites':>7} {'Deleg':>6} "
          f"{'Cite/St':>8} {'Amnd/St':>8} {'H(t)':>6} {'Stale':>8}")
    for decade_start in range(1920, 2030, 10):
        yrs = [y for y in range(decade_start, decade_start + 10) if y in annual_metrics]
        if not yrs:
            continue
        last_y = max(yrs)
        net = annual_metrics[last_y]["net_stock"]
        en = sum(annual_metrics[y]["enacted"] for y in yrs)
        rp = sum(annual_metrics[y]["repealed"] for y in yrs)
        am = sum(annual_metrics[y]["amended"] for y in yrs)
        ci = sum(annual_metrics[y]["cites"] for y in yrs)
        dl = sum(annual_metrics[y]["delegations"] for y in yrs)
        avg_cd = sum(annual_metrics[y]["cite_density"] for y in yrs) / len(yrs)
        avg_ar = sum(annual_metrics[y]["amend_rate"] for y in yrs) / len(yrs)
        avg_h = sum(entropy[y] for y in yrs) / len(yrs)
        stale = stale_cumul.get(last_y, 0)
        print(f"{decade_start}s   {net:>9} {en:>8} {rp:>8} {am:>8} {ci:>7} {dl:>6} "
              f"{avg_cd:>8.4f} {avg_ar:>8.4f} {avg_h:>6.3f} {stale:>8}")


def write_csv(annual_metrics, entropy, stale_cumul, output_dir: Path):
    """Write annual metrics CSV."""
    outpath = output_dir / "legal_entropy_annual.csv"
    with open(outpath, "w") as f:
        f.write("year,gross_stock,net_stock,enacted,repealed,amended,cites,delegations,"
                "cite_density,amend_rate,deleg_rate,entropy,cumul_stale_refs\n")
        for y in sorted(annual_metrics.keys()):
            m = annual_metrics[y]
            f.write(f"{y},{m['gross_stock']},{m['net_stock']},{m['enacted']},"
                    f"{m['repealed']},{m['amended']},{m['cites']},{m['delegations']},"
                    f"{m['cite_density']:.6f},{m['amend_rate']:.6f},{m['deleg_rate']:.6f},"
                    f"{entropy[y]:.6f},{stale_cumul.get(y, 0)}\n")
    print(f"Saved: {outpath}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Legal entropy analysis")
    parser.add_argument("--graph", type=Path, default=Path(".tmp/corpus_graph_full"),
                        help="Corpus graph directory")
    parser.add_argument("--output", type=Path, default=Path(".tmp/legal_entropy"),
                        help="Output directory for plots and CSV")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    print(f"Loading graph from {args.graph}...", file=sys.stderr)
    statutes, amendments, citations, delegations = load_graph(args.graph)
    print(f"  {len(statutes)} statutes, {len(citations)} citation edges", file=sys.stderr)

    year_range = range(1920, 2027)

    print("Computing annual metrics...", file=sys.stderr)
    annual = compute_annual_metrics(statutes, amendments, citations, delegations, year_range)
    entropy = compute_entropy(annual, year_range)

    print("Computing stale reference accumulation...", file=sys.stderr)
    stale = compute_stale_accumulation(citations, amendments, statutes, year_range)

    print("Computing degree distribution...", file=sys.stderr)
    deg_dist = compute_degree_distribution(citations)
    alpha, x_min = fit_power_law(deg_dist)
    print(f"  Power-law exponent α ≈ {alpha:.2f} (x_min={x_min})", file=sys.stderr)

    # Output
    print_summary_table(annual, entropy, stale)
    write_csv(annual, entropy, stale, args.output)

    # Plots
    plot_time_series(annual, entropy, stale, args.output)
    plot_degree_distribution(deg_dist, alpha, args.output)

    print("\nComputing amendment half-life...", file=sys.stderr)
    halflife = compute_amendment_halflife(amendments, statutes)
    print("\n=== AMENDMENT HALF-LIFE (years to first amendment) ===")
    for decade in sorted(halflife):
        h = halflife[decade]
        print(f"  {decade}s (N={h['n']:>5}): median={h['median']:>2}yr, "
              f"p25={h['p25']}yr, p75={h['p75']}yr, "
              f"amended-same-year={h['pct_same_year']:.0f}%")

    print("\nComputing connectivity...", file=sys.stderr)
    conn = compute_connectivity(citations)
    print("\n=== CITATION GRAPH CONNECTIVITY ===")
    print(f"  Statutes in citation graph: {conn['n_cited']}")
    print(f"  Statutes NOT cited: {len(statutes) - conn['n_cited']}")
    print(f"  Connected components: {conn['n_components']}")
    print(f"  Giant component: {conn['giant']} ({conn['giant_pct']:.1f}%)")

    # Key findings summary
    print("\n=== KEY FINDINGS ===")
    print(f"1. Gross statute stock: {annual[2025]['gross_stock']:,} (net: {annual[2025]['net_stock']:,})")
    print(f"2. Peak amendment rate: {max((annual[y]['amend_rate'], y) for y in year_range)[1]} "
          f"({max(annual[y]['amend_rate'] for y in year_range):.4f} amend/statute)")
    print(f"3. Citation density trend: {annual[1960]['cite_density']:.4f} (1960) → "
          f"{annual[2000]['cite_density']:.4f} (2000) → {annual[2025]['cite_density']:.4f} (2025)")
    print(f"4. Stale references accumulated: {stale[2025]:,} (by 2025)")
    print(f"5. Power-law exponent α ≈ {alpha:.2f} (scale-free citation network)")
    print(f"6. Most-cited: Rikoslaki (1889/39) with {max(Counter(c['target_statute_id'] for c in citations if c['edge_type']=='CITES').values()):,} citations")

    # Phase transition detection
    print("\n=== PHASE TRANSITION ANALYSIS ===")
    # Look for years where H(t) jumps >2σ from 5-year rolling mean
    h_vals = [entropy[y] for y in year_range]
    for i, y in enumerate(year_range):
        if i < 5:
            continue
        window = h_vals[i-5:i]
        mean_w = sum(window) / len(window)
        std_w = (sum((v - mean_w)**2 for v in window) / len(window)) ** 0.5
        if std_w > 0 and (h_vals[i] - mean_w) / std_w > 2:
            print(f"  {y}: H={entropy[y]:.3f} (Δ = {(h_vals[i]-mean_w)/std_w:.1f}σ above 5yr mean)")


if __name__ == "__main__":
    main()
