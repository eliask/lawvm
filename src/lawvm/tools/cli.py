"""lawvm — unified developer CLI for LawVM.

Subcommands:
    bisect    <statute_id>  Find which amendment damages a statute's score.
    bisect-section <statute_id>  Find which amendment damages one section.
    dump      <statute_id>  Inspect pipeline state at a named stage.
    source-dump <statute_id>  Inspect raw archived source XML with line numbers.
    inspect-amendment <statute_id>  Inspect one amendment's compile/payload path.
    diagnose-phase <statute_id>  Attribute a structural violation to its first bad pipeline phase.
    invariant-bisect <statute_id>  Find the first amendment that introduces a structural violation.
    snapshot-debug <statute_id>  Inspect timeline snapshots emitted by one amendment.
    product-debug <statute_id>  Inspect timeline entries and materialization for one amendment.
    phase-witness <statute_id>  Emit a machine-readable amendment phase witness for Finland replay.
    oracle-context <statute_id>  Inspect selected Finland oracle locator and version context.
    oracle-text <statute_id>  Fetch oracle consolidated section text at a specific amendment version.
    replay-plan <statute_id>  Inspect replay lineage and oracle selection for one Finland statute.
    trace-section <statute_id>  Show one section before and after one amendment.
    replay-debug <statute_id>  Replay and inspect filtered compiled ops, replay metadata, and event logs.
    replay-inspect <statute_id>  Replay one section and print its IR subtree, text, and metadata.
    classify  <statute_id>  Show typed replay-vs-oracle classification for one statute.
    evidence  <statute_id>  Build a live statute-level proof/evidence bundle.
    prove-oracle <statute_id>  Show only oracle-incorrectness proof claims.
    evidence-review <path>  Review exported proof/evidence artifacts.
    verify    <statute_id>  Run pipeline invariant checks at every stage.
    capture   <statute_id>  Emit amendment-level pipeline capture bundles.
    ops       <statute_id>  List compiled operations with provenance.
    diff      <statute_id>  Provision-level diff: replay vs oracle.
    delegate  <statute_id>  Show delegation clauses (asetuksenantovaltuudet).
    cite      <statute_id>  Show cross-reference edges (CITES/REPEALS/ISSUED_UNDER).
    uk-replay <statute_id>   UK amendment replay with timeline integration.
    eu-replay                   Replay one EU CELEX act against discovered affecting acts and report adjudications.
    eu-reul map|resolve         Bridge CELEX/EULI references to EU retained-law ids.
    scaffold  <jurisdiction> Generate a blocked jurisdiction starter skeleton.
    export                  Batch export graph to Neo4j CSV or JSON-LD.
    coverage  [statute_id]  Corpus coverage audit ("Is The Law Complete?").
    bench-curate           Partition Finland bench corpus into core/suspect/notruth/pending.
    bench-regression-guard Compare saved bench runs and fail on excessive regressions.
    bench-hydrate          Serially hydrate source/oracle cache for a benchmark corpus.
    sync-finlex-latest     Sync latest Finnish PIT XMLs for known statutes into farchive.
    nz-corpus sync          Sync New Zealand API v0 metadata/XML into farchive.
    corrigendum status|apply|classify|report|sources  Corrigendum (oikaisu) inspection and classification.
    audit     formats|staleness|html  Cross-format consistency audit (oracle staleness).
    ee-residual-inventory            Print deterministic EE residual adjudication inventory.
    ee-frontier                      Rank EE bench rows by open vs adjudicated residuals.
    ee-chain-quality                Run consecutive-pair replay quality over an EE version chain.
    ee-pair-status                  Score one EE base/oracle pair with residual-bucket summary.
    ee-explain                      Single-statute deep-dive (divergences + residual buckets + source chain).
    ee-publication-db               Build Estonia divergence SQLite DB from current replayable corpus.
    residual-ledger validate|row    Validate or scaffold Finland residual-ledger CSV rows.
    report query                    Query shared evidence-row JSONL reports.
    destructive-repair-ledger       Emit the seeded Tranche 0 destructive-repair family ledger.
    ee-inspect-source               Inspect one EE source act, target filtering, and compiled ops.
    ee-corpus acquire|curate|current|replayable|stats  Acquire, curate, or show stats for Estonia corpus artifacts.
    export-projections              Export canonical LawVM projections to JSONL/Parquet.
    sql                             Ad-hoc SQL over LawVM projections (DuckDB).
    bench-report                    Summarise a bench run CSV without re-running the bench.
    parse-johto <text>              Parse a Finnish amendment johtolause text and show parsed ops.

Usage:
    lawvm bisect 2006/1299
    lawvm bisect 2006/1299 --verbose
    lawvm bisect-section 2006/1299 --section '63 §'
    lawvm dump 2006/1299 --after parse
    lawvm dump 2006/1299 --after extract --source 2017/794
    lawvm source-dump 2006/1299 --address 'chapter:3/section:12'
    lawvm inspect-amendment 2006/1299 --source 2017/794
    lawvm phase-witness 2006/1299 --source 2017/794 --json
    lawvm oracle-context 2006/1299
    lawvm replay-plan 2006/1299
    lawvm replay-debug 2006/1299 --source 2017/794 --show-clause-text --show-replay-meta
    lawvm trace-section 2006/1299 --source 2017/794 --section '63 §'
    lawvm verify 2006/1299
    lawvm verify 2006/1299 --stage parse
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from lawvm.tools.uk_replay_regime import UK_APPLICABILITY_MODE_CHOICES
from lawvm.tools.uk_replay_regime import add_uk_replay_regime_arguments


def _oracle_version_amendment_id(value: str) -> str:
    if re.fullmatch(r"\d{4}/\d{1,4}", value) is None:
        raise argparse.ArgumentTypeError("expected oracle version amendment id in YYYY/NNN form")
    return value


def _build_parser() -> argparse.ArgumentParser:
    jurisdiction_default = os.environ.get("LAWVM_JURISDICTION", "fi")

    # Root parent provides the default; subcommand parent suppresses its default
    # so `lawvm -j uk evidence-review ...` is not overwritten by the subparser.
    _j_root_parent = argparse.ArgumentParser(add_help=False)
    _j_root_parent.add_argument(
        "-j",
        "--jurisdiction",
        default=jurisdiction_default,
        choices=["fi", "ee", "uk", "no", "nz"],
        help="jurisdiction (default: fi, or LAWVM_JURISDICTION env var)",
    )
    _j_subcommand_parent = argparse.ArgumentParser(add_help=False)
    _j_subcommand_parent.add_argument(
        "-j",
        "--jurisdiction",
        default=argparse.SUPPRESS,
        choices=["fi", "ee", "uk", "no", "nz"],
        help="jurisdiction (default: fi, or LAWVM_JURISDICTION env var)",
    )

    parser = argparse.ArgumentParser(
        prog="lawvm",
        description="LawVM developer tools",
        parents=[_j_root_parent],
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    _P = [_j_subcommand_parent]  # shorthand for parents= below

    # --- bisect ---
    bisect_p = sub.add_parser(
        "bisect",
        help="find which amendment damages a statute's replay score",
        description=(
            "Apply amendments cumulatively, score against final oracle after each "
            "one, report amendments that cause score drops."
        ),
    )
    bisect_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    bisect_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print score for every amendment (not just drops)",
    )
    bisect_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    bisect_p.add_argument(
        "--top",
        type=int,
        default=5,
        help="number of worst drops to show (default: 5)",
    )

    # --- bisect-section ---
    bisect_section_p = sub.add_parser(
        "bisect-section",
        help="find which amendment damages one section against the final oracle",
        description=(
            "Track one section's similarity against the final oracle across the "
            "amendment chain and report the first bad step and worst drops."
        ),
    )
    bisect_section_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    bisect_section_p.add_argument(
        "--section",
        required=True,
        metavar="SECTION",
        help="section filter, e.g. '63 §' or 'chapter:5/section:63'",
    )
    bisect_section_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    bisect_section_p.add_argument(
        "--threshold",
        type=float,
        default=0.9999,
        help="first step below this score is reported as first bad (default: 0.9999)",
    )
    bisect_section_p.add_argument(
        "--top",
        type=int,
        default=5,
        help="number of worst drops to show (default: 5)",
    )
    bisect_section_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print every amendment step",
    )
    bisect_section_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- dump ---
    dump_p = sub.add_parser(
        "dump",
        help="inspect pipeline state at a named stage",
        description=(
            "Show statute state at a pipeline stage. "
            "Default (no --after): full replay body text. "
            "--after parse: base statute structure. "
            "--after extract/normalize: ops from one amendment (requires --source)."
        ),
    )
    dump_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    dump_p.add_argument(
        "--after",
        choices=["parse", "extract", "normalize", "resolve", "apply"],
        help="pipeline stage to dump (default: apply)",
    )
    dump_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="amendment to inspect (required for --after extract/normalize)",
    )
    dump_p.add_argument(
        "--address",
        metavar="ADDR",
        help="filter to one provision, e.g. 'section:9a' or 'chapter:3/section:12'",
    )
    dump_p.add_argument(
        "--before",
        metavar="AMENDMENT_ID",
        help="stop replay before this amendment (temporal PIT): show statute state "
        "as it was immediately before AMENDMENT_ID was applied",
    )

    # --- source-dump ---
    source_dump_p = sub.add_parser(
        "source-dump",
        help="inspect raw archived source XML with line numbers",
        description=(
            "Read source XML from the corpus archive and print the whole document "
            "or a targeted section/chapter/part subtree with line numbers."
        ),
    )
    source_dump_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    source_dump_p.add_argument(
        "--address",
        metavar="ADDR",
        help="optional source address filter, e.g. 'section:12' or 'chapter:3/section:12'",
    )
    source_dump_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- inspect-amendment ---
    inspect_amendment_p = sub.add_parser(
        "inspect-amendment",
        help="inspect one amendment's compile and payload-normalization path",
        description=(
            "Show the working johtolause, compiled ops, per-target payload "
            "normalization, subsection mapping, and source pathologies for one amendment."
        ),
    )
    inspect_amendment_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    inspect_amendment_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment to inspect, e.g. 2017/794",
    )
    inspect_amendment_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for the parent state before this amendment (default: legal_pit)",
    )
    inspect_amendment_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    inspect_amendment_p.add_argument(
        "--stage",
        default="all",
        choices=["all", "source", "compile", "groups"],
        help=(
            "limit output to one inspection stage: source=parse/normalize, "
            "compile=compiled ops/projection rows, groups=per-target payload normalization"
        ),
    )
    inspect_amendment_p.add_argument(
        "--show-source-normalization-facts",
        action="store_true",
        help="include source-normalization facts in text output",
    )

    # --- diagnose-phase ---
    diagnose_phase_p = sub.add_parser(
        "diagnose-phase",
        help="attribute a structural violation to its first bad pipeline phase",
        description=(
            "For one statute and one amendment, run a structural detector at each "
            "pipeline phase (before_state, direct_applied, replay_fold, materialized) "
            "and report the first phase where the detector fires.  "
            "Use invariant-bisect first to find the amendment, then diagnose-phase "
            "to attribute the phase."
        ),
    )
    diagnose_phase_p.add_argument("statute_id", help="statute ID, e.g. 1995/398")
    diagnose_phase_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment to diagnose, e.g. 2013/982",
    )
    diagnose_phase_p.add_argument(
        "--target",
        metavar="PATH",
        default="",
        help=(
            "optional structural path filter, e.g. 'chapter:4/section:20'; "
            "only violations whose path contains this segment are shown"
        ),
    )
    diagnose_phase_p.add_argument(
        "--detector",
        default="duplicate_label",
        choices=["duplicate_label", "illegal_edge", "all_tree", "text_duplication", "flattened_sublist_family"],
        help="structural detector to run (default: duplicate_label)",
    )
    diagnose_phase_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    diagnose_phase_p.add_argument(
        "--first-bad-amendment",
        metavar="AMENDMENT_ID",
        default="",
        help=(
            "pre-computed first-bad-amendment from invariant-bisect; "
            "included in --certificate output"
        ),
    )
    diagnose_phase_p.add_argument(
        "--certificate",
        action="store_true",
        help=(
            "emit a compact machine-readable JSON certificate "
            "(statute_id, target, detector, first_bad_amendment, first_bad_phase, "
            "confidence, evidence)"
        ),
    )
    diagnose_phase_p.add_argument(
        "--json",
        action="store_true",
        help="emit full JSON bundle",
    )

    # --- invariant-bisect ---
    invariant_bisect_p = sub.add_parser(
        "invariant-bisect",
        help="find the first amendment that introduces a structural violation",
        description=(
            "Scan the amendment chain of one statute, applying each amendment "
            "cumulatively and running a structural detector after each step.  "
            "Reports the first bad amendment, monotone/transient classification, "
            "and the concrete violations at the first failure point."
        ),
    )
    invariant_bisect_p.add_argument("statute_id", help="statute ID, e.g. 1995/398")
    invariant_bisect_p.add_argument(
        "--target",
        metavar="PATH",
        default="",
        help=(
            "optional structural path filter, e.g. 'chapter:4/section:20'; "
            "only violations whose path contains this segment are considered"
        ),
    )
    invariant_bisect_p.add_argument(
        "--detector",
        default="duplicate_label",
        choices=["duplicate_label", "illegal_edge", "all_tree", "text_duplication", "flattened_sublist_family"],
        help="structural detector to run (default: duplicate_label)",
    )
    invariant_bisect_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    invariant_bisect_p.add_argument(
        "--after",
        metavar="AMENDMENT_ID",
        default="",
        help="start scan after this amendment ID (exclusive)",
    )
    invariant_bisect_p.add_argument(
        "--before",
        metavar="AMENDMENT_ID",
        default="",
        help="stop scan before this amendment ID (exclusive)",
    )
    invariant_bisect_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-amendment clean/bad status",
    )
    invariant_bisect_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON (includes full per-step detail)",
    )

    # --- snapshot-debug ---
    snapshot_debug_p = sub.add_parser(
        "snapshot-debug",
        help="inspect timeline snapshots emitted by one amendment",
        description=(
            "Show the LegalOperation snapshots that process_muutoslaki emits for "
            "one amendment, optionally filtered to a target address.  Disambiguates "
            "direct_applied_state from the emitted snapshot payload."
        ),
    )
    snapshot_debug_p.add_argument("statute_id", help="statute ID, e.g. 1995/398")
    snapshot_debug_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment to inspect, e.g. 2013/982",
    )
    snapshot_debug_p.add_argument(
        "--target",
        metavar="PATH",
        default="",
        help=(
            "optional target address filter, e.g. 'section:20' or "
            "'chapter:4/section:20'"
        ),
    )
    snapshot_debug_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    snapshot_debug_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- product-debug ---
    product_debug_p = sub.add_parser(
        "product-debug",
        help="inspect timeline entries and materialization for one amendment",
        description=(
            "For one statute and one amendment, show the ProvisionTimeline entries "
            "that affect a target address, the active-version selection at the replay "
            "cutoff date, migration events, and the materialized text.  Diagnoses the "
            "'direct_applied_state clean, materialized bad' class of bugs."
        ),
    )
    product_debug_p.add_argument("statute_id", help="statute ID, e.g. 1995/398")
    product_debug_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment to inspect, e.g. 2013/982",
    )
    product_debug_p.add_argument(
        "--target",
        metavar="PATH",
        default="",
        help=(
            "optional target address filter, e.g. 'section:20' or "
            "'chapter:4/section:20'"
        ),
    )
    product_debug_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    product_debug_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- phase-witness ---
    phase_witness_p = sub.add_parser(
        "phase-witness",
        help="emit a machine-readable amendment phase witness for Finland replay",
        description=(
            "Build one bounded Tranche 0 debug artifact for a Finland replay amendment: "
            "source-lane choice, clause/effect surface, payload surface, lowered ops, "
            "findings, temporal/migration events, replay-fold witness, and materialization "
            "selection summary."
        ),
    )
    phase_witness_p.add_argument("statute_id", help="statute ID, e.g. 1962/184")
    phase_witness_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment to inspect, e.g. 1967/551",
    )
    phase_witness_p.add_argument(
        "--target",
        metavar="PATH",
        default="",
        help="optional target path filter, e.g. 'section:17' or 'chapter:2/section:17'",
    )
    phase_witness_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    phase_witness_p.add_argument(
        "--output",
        metavar="PATH",
        help="optional path to write the JSON witness artifact",
    )
    phase_witness_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- oracle-context ---
    oracle_context_p = sub.add_parser(
        "oracle-context",
        help="inspect selected Finland oracle locator and version context",
        description=(
            "Print the selected consolidated-oracle locator, embedded version id, "
            "cutoff/consolidated date, and selector mode for one Finnish statute."
        ),
    )
    oracle_context_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    oracle_context_p.add_argument(
        "--selector-mode",
        default="latest_cached_editorial",
        choices=[
            "latest_cached_editorial",
            "bench_comparable",
            "exact_embedded_version",
            "date_consolidated_at_or_before",
        ],
        help="consolidated selector mode (default: latest_cached_editorial)",
    )
    oracle_context_p.add_argument(
        "--version-tag",
        metavar="YYYYNNNN",
        help="exact embedded version tag for exact_embedded_version",
    )
    oracle_context_p.add_argument(
        "--cutoff",
        metavar="YYYY-MM-DD",
        help="cutoff date for date_consolidated_at_or_before",
    )
    oracle_context_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- oracle-text ---
    oracle_text_p = sub.add_parser(
        "oracle-text",
        help="fetch oracle consolidated section text at a specific amendment version",
        description=(
            "Read the Finnish consolidated oracle XML (sd-cons) from the archive "
            "at either the current oracle version or at the version pinned to a "
            "specific amendment, and print the section text with optional subsection "
            "breakdown.  Covers the gap where farchive cat + regex was the only way "
            "to inspect oracle section text at a specific consolidated version snapshot."
        ),
    )
    oracle_text_p.add_argument("statute_id", help="statute ID, e.g. 2017/530")
    oracle_text_p.add_argument(
        "--section",
        metavar="ADDR",
        default="",
        help="section address, e.g. 'section:2'. If omitted, lists all section labels.",
    )
    oracle_text_p.add_argument(
        "--at-amendment",
        metavar="AMENDMENT_ID",
        default="",
        help=(
            "read oracle at the consolidated version pinned to this amendment "
            "(e.g. '2020/959' → version tag '20200959'). "
            "Default: use current selected oracle."
        ),
    )
    oracle_text_p.add_argument(
        "--subsections",
        action="store_true",
        help="show per-subsection text breakdown",
    )
    oracle_text_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- replay-plan ---
    replay_plan_p = sub.add_parser(
        "replay-plan",
        help="inspect Finland replay lineage and oracle selection",
        description=(
            "Show the prepared replay plan for one Finland statute, including "
            "the amendment chain, replay cutoff, and selected consolidated oracle context."
        ),
    )
    replay_plan_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    replay_plan_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode used to prepare the plan (default: finlex_oracle)",
    )
    replay_plan_p.add_argument(
        "--selector-mode",
        default="latest_cached_editorial",
        choices=[
            "latest_cached_editorial",
            "bench_comparable",
            "exact_embedded_version",
            "date_consolidated_at_or_before",
        ],
        help="consolidated selector mode (default: latest_cached_editorial)",
    )
    replay_plan_p.add_argument(
        "--version-tag",
        metavar="YYYYNNNN",
        help="exact embedded version tag for exact_embedded_version",
    )
    replay_plan_p.add_argument(
        "--cutoff",
        metavar="YYYY-MM-DD",
        help="cutoff date for date_consolidated_at_or_before",
    )
    replay_plan_p.add_argument(
        "--strict",
        action="store_true",
        help="prepare the plan with the current Finland ingestion strict profile",
    )
    replay_plan_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- trace-section ---
    trace_section_p = sub.add_parser(
        "trace-section",
        help="show one section immediately before and after one amendment",
        description=(
            "Replay the parent statute to the boundary before one amendment and "
            "to the boundary immediately after it, then print the chosen section "
            "before/after plus the final oracle text for context."
        ),
    )
    trace_section_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    trace_section_p.add_argument(
        "--source",
        required=True,
        metavar="AMENDMENT_ID",
        help="amendment boundary to inspect, e.g. 2017/794",
    )
    trace_section_p.add_argument(
        "--section",
        required=True,
        metavar="SECTION",
        help="section filter, e.g. '63 §' or 'chapter:5/section:63'",
    )
    trace_section_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    trace_section_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- evidence ---
    evidence_p = sub.add_parser(
        "evidence",
        help="build a live statute-level proof bundle",
        description=(
            "Join current replay/oracle classification, HTML-vs-XML topology, "
            "strict replay status, and corrigendum provenance into one auditable "
            "statute-level evidence bundle."
        ),
    )
    evidence_p.add_argument("statute_id", nargs="+", help="statute ID(s), e.g. 1991/1707")
    evidence_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for live evidence building (default: legal_pit)",
    )
    evidence_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    evidence_p.add_argument(
        "--markdown",
        action="store_true",
        help="emit a reviewer-oriented Markdown report",
    )
    evidence_p.add_argument(
        "--output",
        metavar="PATH",
        help="write the evidence bundle to PATH (.json for one statute, .jsonl for multi-statute or explicit .jsonl)",
    )
    add_uk_replay_regime_arguments(evidence_p, include_metadata_only_effects=True)

    # --- prove-oracle ---
    prove_oracle_p = sub.add_parser(
        "prove-oracle",
        help="show live oracle-incorrectness proof claims for one statute",
        description=(
            "Filter the full statute evidence bundle down to claims that currently "
            "support oracle-side incorrectness, such as stale section state or "
            "HTML-vs-XML topology drift."
        ),
    )
    prove_oracle_p.add_argument("statute_id", nargs="+", help="statute ID(s), e.g. 1991/1707")
    prove_oracle_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for live evidence building (default: legal_pit)",
    )
    prove_oracle_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    prove_oracle_p.add_argument(
        "--markdown",
        action="store_true",
        help="emit a reviewer-oriented Markdown report",
    )
    prove_oracle_p.add_argument(
        "--output",
        metavar="PATH",
        help="write the oracle-proof bundle to PATH (.json for one statute, .jsonl for multi-statute or explicit .jsonl)",
    )
    prove_oracle_p.add_argument(
        "--with-bisect",
        action="store_true",
        help="include section bisect support when building oracle proof bundles",
    )
    add_uk_replay_regime_arguments(prove_oracle_p, include_metadata_only_effects=True)

    # --- evidence-review ---
    evidence_review_p = sub.add_parser(
        "evidence-review",
        parents=_P,
        help="review exported proof/evidence JSON or JSONL artifacts",
        description=(
            "Load saved evidence/proof artifacts or build live bundles for selected "
            "statutes, then summarize them by proof tier, claim kind, and trigger "
            "observations."
        ),
    )
    evidence_review_p.add_argument("artifact_path", nargs="*", help="JSON or JSONL artifact path(s)")
    evidence_review_p.add_argument(
        "--statute-id",
        nargs="+",
        help="build and review live evidence bundles for these statute IDs instead of reading artifact files",
    )
    evidence_review_p.add_argument(
        "--oracle-corpus",
        action="store_true",
        help="build and review live evidence bundles for the full consolidated-oracle statute corpus",
    )
    evidence_review_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for live statute review (default: legal_pit)",
    )
    evidence_review_p.add_argument(
        "--with-bisect",
        action="store_true",
        help="include section-bisect payloads when building live evidence bundles",
    )
    evidence_review_p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel workers for live review bundle building in --statute-id and --oracle-corpus modes (default: 1)",
    )
    evidence_review_p.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="oracle-corpus mode: statutes per checkpoint chunk (default: 200)",
    )
    evidence_review_p.add_argument(
        "--min-year",
        type=int,
        default=0,
        help="oracle-corpus mode: minimum statute year (default: no lower bound)",
    )
    evidence_review_p.add_argument(
        "--max-year",
        type=int,
        default=0,
        help="oracle-corpus mode: maximum statute year (default: no upper bound)",
    )
    evidence_review_p.add_argument(
        "--start-at",
        type=int,
        default=0,
        help="oracle-corpus mode: start at this 0-based statute index (default: 0)",
    )
    evidence_review_p.add_argument(
        "--max-statutes",
        type=int,
        default=0,
        help="oracle-corpus mode: process at most this many statutes (default: all)",
    )
    evidence_review_p.add_argument(
        "--cache-only",
        action="store_true",
        help="use cached archive/transparent corpus data only; do not live-refresh Finlex during review",
    )
    evidence_review_p.add_argument(
        "--bundle-cache-dir",
        default="",
        help="reuse per-statute evidence bundles from this directory during live review; oracle-corpus mode defaults to .tmp/evidence_bundle_cache",
    )
    add_uk_replay_regime_arguments(evidence_review_p, include_metadata_only_effects=True)
    evidence_review_p.add_argument(
        "--corpus-store",
        default="",
        choices=["", "auto", "zip", "transparent", "archive"],
        help="live review corpus backend override (default: current repo auto-detect)",
    )
    evidence_review_p.add_argument(
        "--progress-path",
        default="",
        help="oracle-corpus mode: append per-chunk progress JSONL to this path",
    )
    evidence_review_p.add_argument(
        "--output",
        default="",
        help="oracle-corpus mode: write/update JSON snapshot at this path",
    )
    evidence_review_p.add_argument(
        "--resume",
        action="store_true",
        help="oracle-corpus mode: resume from existing --output snapshot when possible",
    )
    evidence_review_p.add_argument(
        "--primary-tier",
        default="",
        help="keep only bundles whose primary proof tier matches this exact value",
    )
    evidence_review_p.add_argument("--tier", default="", help="keep only bundles containing this proof tier")
    evidence_review_p.add_argument("--kind", default="", help="keep only bundles containing this proof-claim kind")
    evidence_review_p.add_argument(
        "--section-kind",
        default="",
        help="keep only bundles containing this selected section-claim kind",
    )
    evidence_review_p.add_argument(
        "--section-rule",
        default="",
        help="keep only bundles containing this selected section-claim inference rule",
    )
    evidence_review_p.add_argument(
        "--strict-fail-reason",
        default="",
        help="keep only bundles containing this strict fail reason",
    )
    evidence_review_p.add_argument(
        "--frontend-observation-kind",
        default="",
        help="keep only bundles containing this frontend observation kind",
    )
    evidence_review_p.add_argument(
        "--frontend-leftovers-only",
        action="store_true",
        help="keep only bundles with nonzero frontend sparse-payload leftovers",
    )
    evidence_review_p.add_argument(
        "--frontend-sparse-blocker-source",
        default="",
        help="keep only bundles containing this sparse blocker source statute",
    )
    evidence_review_p.add_argument(
        "--frontend-sparse-blocker-section",
        default="",
        help="keep only bundles containing this sparse blocker section",
    )
    evidence_review_p.add_argument(
        "--payload-completeness-kind",
        default="",
        help="keep only bundles containing this payload completeness kind",
    )
    evidence_review_p.add_argument(
        "--payload-tail-policy",
        default="",
        help="keep only bundles containing this payload tail policy",
    )
    evidence_review_p.add_argument(
        "--provenance-projection-kind",
        default="",
        help="keep only bundles containing this provenance projection kind",
    )
    evidence_review_p.add_argument(
        "--provenance-tag",
        default="",
        help="keep only bundles containing this provenance projection tag",
    )
    evidence_review_p.add_argument(
        "--provenance-source-statute",
        default="",
        help="keep only bundles containing this provenance source statute",
    )
    evidence_review_p.add_argument(
        "--source-proof-kind",
        default="",
        help="keep only bundles containing this source-proof claim kind",
    )
    evidence_review_p.add_argument(
        "--source-pathology-code",
        default="",
        help="keep only bundles containing this source pathology code",
    )
    evidence_review_p.add_argument(
        "--source-pathology-source",
        default="",
        help="keep only bundles containing this source pathology source statute",
    )
    evidence_review_p.add_argument(
        "--source-pathology-target-label",
        default="",
        help="keep only bundles containing this source pathology target label",
    )
    evidence_review_p.add_argument(
        "--source-pathology-diagnostic-reason",
        default="",
        help="keep only bundles containing this source pathology diagnostic reason",
    )
    evidence_review_p.add_argument(
        "--alternative-replay-section",
        default="",
        help="keep only bundles containing this alternative replay section match",
    )
    evidence_review_p.add_argument(
        "--html-noncommensurable-reason",
        default="",
        help="keep only bundles containing this HTML/XML noncommensurable reason",
    )
    evidence_review_p.add_argument(
        "--evidence-context-degraded",
        action="store_true",
        help="keep only bundles where an evidence-context rail degraded",
    )
    evidence_review_p.add_argument(
        "--evidence-context-rail",
        default="",
        help="keep only bundles where this evidence-context rail degraded",
    )
    evidence_review_p.add_argument(
        "--trigger-source", default="", help="keep only bundles with this trigger observation source"
    )
    evidence_review_p.add_argument(
        "--trigger-field", default="", help="keep only bundles with this trigger observation field"
    )
    evidence_review_p.add_argument(
        "--actionable-unresolved-only",
        action="store_true",
        help="keep only unresolved rows that still look like actionable compiler/frontend debt",
    )
    evidence_review_p.add_argument(
        "--nontrivial-unresolved-only",
        action="store_true",
        help="keep only unresolved rows that are not just trivially_empty",
    )
    evidence_review_p.add_argument(
        "--mixed-replay-risk-only",
        action="store_true",
        help="keep only non-primary-replay rows that still carry replay divergence plus strong stack-owned strict-fail signals",
    )
    evidence_review_p.add_argument(
        "--ready-oracle-artifacts-only",
        action="store_true",
        help="keep only bundles with at least one oracle-proof artifact marked ready_for_clean_v1",
    )
    evidence_review_p.add_argument(
        "--oracle-artifact-family",
        default="",
        help="keep only rows whose oracle artifact families include this family",
    )
    evidence_review_p.add_argument(
        "--oracle-artifact-gap",
        default="",
        help="keep only rows whose oracle artifact gaps include this gap",
    )
    evidence_review_p.add_argument("--limit", type=int, default=20, help="max rows to emit (default: 20)")
    evidence_review_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- capture ---
    capture_p = sub.add_parser(
        "capture",
        help="emit amendment-level pipeline capture bundles",
        description=(
            "Compile a statute and emit a JSON bundle grouped by amendment source: "
            "lineage metadata, body-shape summaries, compiled ops, canonical/recovered "
            "ops, failures, and adjudications."
        ),
    )
    capture_p.add_argument("statute_id", help="statute ID, e.g. 1992/480")
    capture_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    capture_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="restrict output to one amendment in the lineage",
    )
    capture_p.add_argument(
        "--output",
        metavar="PATH",
        help="write JSON to PATH instead of stdout",
    )

    # --- explain ---
    explain_p = sub.add_parser(
        "explain",
        parents=_P,
        help="divergence explainer: blame + diff + johtolause + diagnosis",
        description=(
            "For each diverging provision, shows the last amendment to touch it, "
            "the johtolause text, the divergence snippet, and an auto-diagnosis "
            "(ORACLE_STALE / REPLAY_EXTRA / REPLAY_MISSING / EDITORIAL_CONVENTION / UNKNOWN)."
        ),
    )
    explain_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    explain_p.add_argument(
        "--section",
        metavar="SECTION",
        help="filter to one section, e.g. '63 §'",
    )
    explain_p.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="only explain sections below this similarity (default: 1.0 = all imperfect)",
    )
    explain_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    explain_p.add_argument(
        "--oracle-selector-mode",
        default="latest_cached_editorial",
        choices=["latest_cached_editorial", "bench_comparable"],
        help=(
            "oracle selector mode for the consolidated Finland witness "
            "(default: latest_cached_editorial)"
        ),
    )
    explain_p.add_argument(
        "--oracle-version-amendment-id",
        type=_oracle_version_amendment_id,
        default=None,
        metavar="YYYY/NNN",
        help=(
            "select the exact consolidated oracle by amendment id; takes precedence over "
            "--oracle-selector-mode"
        ),
    )
    explain_p.add_argument(
        "--compile-summary",
        dest="compile_summary",
        action="store_true",
        help="show compatibility compile summary (canonical/recovered/failed ops, adjudications, strictness)",
    )
    explain_p.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        help=(
            "run in strict mode (FINLAND_INGESTION_V1 profile): heuristics that the "
            "profile forbids are skipped and recorded as adjudications."
        ),
    )
    explain_p.add_argument(
        "--facade",
        dest="facade",
        action="store_true",
        help=(
            "show CompileFacade summary (observations, temporal_events, quirks_used, "
            "source_completeness_issues, strictness) built from the replay PhaseResult"
        ),
    )
    explain_p.add_argument(
        "--oracle-id",
        metavar="ID",
        default="",
        help="[-j ee] explicit EE oracle/consolidated aktViide",
    )
    explain_p.add_argument(
        "--json",
        action="store_true",
        help="[-j ee] emit JSON",
    )

    # --- classify ---
    classify_p = sub.add_parser(
        "classify",
        help="typed replay-vs-oracle classification for one statute",
        description=(
            "Public one-statute wrapper over oracle-check classification. "
            "Shows section diagnoses, source pathologies, and contingent effective-date sources."
        ),
    )
    classify_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    classify_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    classify_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- bench ---
    from lawvm.tools.bench import register_cli as _register_bench

    _register_bench(sub, _j_subcommand_parent)

    # --- blame ---
    blame_p = sub.add_parser(
        "blame",
        parents=_P,
        help="per-provision last-modification trace",
        description=(
            "Annotate each provision with the amendment that last modified it. Like git blame for statute provisions."
        ),
    )
    blame_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    blame_p.add_argument(
        "--address",
        metavar="ADDR",
        help="filter to one provision, e.g. 'section:9a'",
    )
    blame_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="only show provisions last-touched by this amendment",
    )
    blame_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    blame_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="YYYY-MM-DD",
        help="[-j ee] target date for PIT replay",
    )
    blame_p.add_argument(
        "--matrix",
        action="store_true",
        help="[-j ee] show per-amendment change matrix",
    )
    blame_p.add_argument(
        "--archive",
        metavar="DB",
        help="[-j ee] Farchive DB path",
    )

    # --- replay ---
    replay_p = sub.add_parser(
        "replay",
        parents=_P,
        help="point-in-time amendment replay (use -j to select jurisdiction)",
    )
    replay_p.add_argument("base_id", metavar="ID", help="base act identifier or local XML path")
    replay_p.add_argument(
        "--as-of", dest="as_of", required=True, metavar="YYYY-MM-DD", help="target date for amendments"
    )
    replay_p.add_argument("--archive", metavar="DB", help="[-j ee] Farchive DB path; [-j no] Norway source path (farchive DB or legacy dir)")
    replay_p.add_argument("--index", metavar="FILE", help="[-j no] prebuilt Norway amendment index JSON")
    replay_p.add_argument("--commencement", metavar="FILE", help="[-j no] Norway commencement override JSON")
    replay_p.add_argument("--verbose", "-v", action="store_true")
    replay_p.add_argument("--show-text", action="store_true", dest="show_text")
    replay_p.add_argument("--json", action="store_true", help="emit JSON")
    replay_p.add_argument(
        "--replay-adjudication-samples",
        nargs="+",
        metavar="KIND",
        help="[-j uk] in text mode, print bounded samples for these replay adjudication kinds",
    )
    replay_p.add_argument(
        "--replay-adjudication-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="[-j uk] maximum replay adjudication samples to print in text mode (default: 5)",
    )
    add_uk_replay_regime_arguments(replay_p)

    # --- no-index ---
    no_index_p = sub.add_parser(
        "no-index",
        help="build a Norway amendment index from the Norway source store",
    )
    no_index_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy tar directory",
    )
    no_index_p.add_argument(
        "--output",
        metavar="FILE",
        help="write the index JSON to FILE",
    )
    no_index_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before emitting/saving index",
    )
    no_index_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON to stdout",
    )

    # --- no-ingest ---
    no_ingest_p = sub.add_parser(
        "no-ingest",
        help="hydrate norway.farchive from local Lovdata public tarballs",
    )
    no_ingest_p.add_argument(
        "--data-dir",
        metavar="DIR",
        required=True,
        help="directory containing Norway public tarballs",
    )
    no_ingest_p.add_argument(
        "--db",
        metavar="PATH",
        help="destination farchive DB path (default: data/norway.farchive)",
    )
    no_ingest_p.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip locators already present in the destination farchive",
    )
    no_ingest_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-statsrad ---
    no_statsrad_p = sub.add_parser(
        "no-statsrad",
        help="fetch and extract Offisielt fra statsrad evidence into norway.farchive",
    )
    no_statsrad_p.add_argument(
        "--db",
        metavar="PATH",
        help="Norway farchive DB path (default: data/norway.farchive)",
    )
    no_statsrad_p.add_argument(
        "--start-page",
        type=int,
        default=1,
        metavar="N",
        help="first listing page to fetch (default: 1)",
    )
    no_statsrad_p.add_argument(
        "--bulletin-id",
        action="append",
        metavar="ID",
        help="restrict fetch/extract to one or more bulletin ids",
    )
    no_statsrad_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit fetched bulletins after manifest filtering",
    )
    no_statsrad_p.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        metavar="H",
        help="listing-page cache freshness window in hours (default: 24)",
    )
    no_statsrad_p.add_argument(
        "--skip-existing",
        action="store_true",
        help="reuse stored listing-page HTML and bulletin artifacts if present",
    )
    no_statsrad_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-report ---
    no_commencement_p = sub.add_parser(
        "no-commencement-report",
        help="report unresolved Norway commencement cases from local/indexed data",
    )
    no_commencement_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="directory containing lovtidend-avd1-*.tar.bz2",
    )
    no_commencement_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_p.add_argument(
        "--base-id",
        metavar="ID",
        help="filter unresolved commencement cases to one Norway base act id",
    )
    no_commencement_p.add_argument(
        "--phrase",
        metavar="TEXT",
        help="filter unresolved commencement cases to one normalized phrase family",
    )
    no_commencement_p.add_argument(
        "--override-state",
        choices=["blank", "untracked", "resolved"],
        help="filter unresolved commencement cases by override progress state",
    )
    no_commencement_p.add_argument(
        "--current-laws-only",
        action="store_true",
        help="keep only unresolved commencement cases that affect current laws",
    )
    no_commencement_p.add_argument(
        "--sort",
        choices=["source", "impact", "unlock"],
        default="source",
        help="order report entries by source id, by current-law impact, or by immediate unlock potential",
    )
    no_commencement_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_commencement_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit printed entries",
    )
    no_commencement_p.add_argument(
        "--template-output",
        metavar="FILE",
        help="write a JSON override template for the reported unresolved entries",
    )
    no_commencement_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-blockers ---
    no_blockers_p = sub.add_parser(
        "no-blockers",
        help="report current Norway laws blocked by unresolved commencement",
    )
    no_blockers_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_blockers_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_blockers_p.add_argument(
        "--base-id",
        metavar="ID",
        help="restrict the blocker report to one Norway base act id",
    )
    no_blockers_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_blockers_p.add_argument(
        "--min-blockers",
        type=int,
        default=1,
        metavar="N",
        help="show only laws blocked by at least N unresolved amendment acts",
    )
    no_blockers_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit printed laws",
    )
    no_blockers_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-source ---
    no_source_p = sub.add_parser(
        "no-source",
        help="inspect one Norway amendment source and the current laws it affects",
    )
    no_source_p.add_argument("source_id", metavar="ID", help="Norway amendment source id")
    no_source_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_source_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_source_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_source_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit listed affected laws",
    )
    no_source_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-source-excerpt ---
    no_source_excerpt_p = sub.add_parser(
        "no-source-excerpt",
        help="show bounded Norway source excerpts for one or more literal needles",
    )
    no_source_excerpt_p.add_argument("source_id", metavar="ID", help="Norway source id")
    no_source_excerpt_p.add_argument("needles", nargs="+", metavar="TEXT", help="literal needle(s) to search for")
    no_source_excerpt_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_source_excerpt_p.add_argument(
        "--mode",
        choices=["auto", "current", "original", "amendment"],
        default="auto",
        help="source selection mode (default: auto)",
    )
    no_source_excerpt_p.add_argument(
        "--context",
        type=int,
        default=160,
        metavar="N",
        help="characters of context on each side (default: 160)",
    )
    no_source_excerpt_p.add_argument(
        "--max-hits",
        type=int,
        default=5,
        metavar="N",
        help="maximum matches per needle (default: 5)",
    )
    no_source_excerpt_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-law ---
    no_law_p = sub.add_parser(
        "no-law",
        help="inspect one Norway law across indexed amendment sources",
    )
    no_law_p.add_argument("base_id", metavar="ID", help="Norway base act id")
    no_law_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_law_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_law_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_law_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit listed amendment sources",
    )
    no_law_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-op-trace ---
    no_op_trace_p = sub.add_parser(
        "no-op-trace",
        help="inspect Norway amendment ops touching one or more provision paths",
    )
    no_op_trace_p.add_argument("base_id", metavar="ID", help="Norway base act id")
    no_op_trace_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_op_trace_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_op_trace_p.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="PATH",
        help="path filter in kind:label[/kind:label...] form",
    )
    no_op_trace_p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="bound displayed sources and ops (default: 20)",
    )
    no_op_trace_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-missing-base ---
    no_missing_base_p = sub.add_parser(
        "no-missing-base",
        help="report amended current Norway laws missing a local original base source",
    )
    no_missing_base_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_missing_base_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_missing_base_p.add_argument(
        "--base-id",
        metavar="ID",
        help="restrict the report to one Norway base act id",
    )
    no_missing_base_p.add_argument(
        "--min-amendments",
        type=int,
        default=1,
        metavar="N",
        help="show only laws with at least N indexed amendments",
    )
    no_missing_base_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit printed laws",
    )
    no_missing_base_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-validate ---
    no_commencement_validate_p = sub.add_parser(
        "no-commencement-validate",
        help="validate a Norway commencement override JSON against the current index",
    )
    no_commencement_validate_p.add_argument(
        "--commencement",
        metavar="FILE",
        required=True,
        help="Norway commencement override JSON to validate",
    )
    no_commencement_validate_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_commencement_validate_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_validate_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-phrases ---
    no_commencement_phrases_p = sub.add_parser(
        "no-commencement-phrases",
        help="group unresolved Norway commencement cases by normalized phrase",
    )
    no_commencement_phrases_p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_commencement_phrases_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_phrases_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_commencement_phrases_p.add_argument(
        "--current-laws-only",
        action="store_true",
        default=True,
        help="keep only phrases that still affect current Norway laws (default: true)",
    )
    no_commencement_phrases_p.add_argument(
        "--phrase",
        metavar="TEXT",
        help="restrict the report to one normalized phrase family",
    )
    no_commencement_phrases_p.add_argument(
        "--override-state",
        choices=["blank", "untracked", "resolved"],
        help="restrict the report to one override progress state",
    )
    no_commencement_phrases_p.add_argument(
        "--sort",
        choices=["source", "impact", "unlock"],
        default="unlock",
        help="order phrase groups alphabetically, by executable impact, or by executable unlock value",
    )
    no_commencement_phrases_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit printed phrase groups",
    )
    no_commencement_phrases_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-impact ---
    no_impact_p = sub.add_parser(
        "no-impact",
        help="quantify the replayability impact of a Norway commencement override file",
    )
    no_impact_p.add_argument(
        "--commencement",
        metavar="FILE",
        required=True,
        help="Norway commencement override JSON to evaluate",
    )
    no_impact_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_impact_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_impact_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit listed unlocked laws",
    )
    no_impact_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-inventory ---
    no_inventory_p = sub.add_parser(
        "no-inventory",
        help="Norway replayability inventory from the local Farchive-backed source layer",
    )
    no_inventory_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_inventory_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_inventory_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON",
    )
    no_inventory_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-frontier ---
    no_frontier_p = sub.add_parser(
        "no-frontier",
        help="compact Norway frontier summary across executable and source blockers",
    )
    no_frontier_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_frontier_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_frontier_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before summarizing",
    )
    no_frontier_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for the consistency sample (default: 2026-03-29)",
    )
    no_frontier_p.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="limit listed queue items in each section",
    )
    no_frontier_p.add_argument(
        "--min-blockers",
        type=int,
        default=3,
        metavar="N",
        help="minimum contingent blockers for the executable blocker section",
    )
    no_frontier_p.add_argument(
        "--min-amendments",
        type=int,
        default=1,
        metavar="N",
        help="minimum amendments for the missing-base section",
    )
    no_frontier_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-divergence ---
    no_divergence_p = sub.add_parser(
        "no-divergence",
        help="explain Norway replay-vs-current divergences for one law",
    )
    no_divergence_p.add_argument("base_id", metavar="ID", help="Norway base act id")
    no_divergence_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_divergence_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_divergence_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_divergence_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before verifying",
    )
    no_divergence_p.add_argument(
        "--max-divergences",
        type=int,
        default=10,
        metavar="N",
        help="include at most N primary divergences (default: 10)",
    )
    no_divergence_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-coverage ---
    no_coverage_p = sub.add_parser(
        "no-coverage",
        help="attribute Norway divergences to touched replay paths vs untouched drift",
    )
    no_coverage_p.add_argument("base_id", metavar="ID", help="Norway base act id")
    no_coverage_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_coverage_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_coverage_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_coverage_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before verifying",
    )
    no_coverage_p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="bound displayed touched paths and divergences (default: 20)",
    )
    no_coverage_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-debug ---
    no_debug_p = sub.add_parser(
        "no-debug",
        help="compact Norway combined replay/source/op debug report",
    )
    no_debug_p.add_argument("base_id", metavar="ID", help="Norway base act id")
    no_debug_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_debug_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_debug_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_debug_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before debugging",
    )
    no_debug_p.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="PATH",
        help="optional path filter(s) for the op-trace portion",
    )
    no_debug_p.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="bound divergences, sources, and ops (default: 5)",
    )
    no_debug_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-workqueue ---
    no_workqueue_p = sub.add_parser(
        "no-workqueue",
        help="prioritized Norway commencement-resolution work queue",
    )
    no_workqueue_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_workqueue_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_workqueue_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_workqueue_p.add_argument(
        "--current-laws-only",
        action="store_true",
        default=True,
        help="keep only queue items that affect current Norway laws (default: true)",
    )
    no_workqueue_p.add_argument(
        "--sort",
        choices=["source", "impact", "unlock"],
        default="unlock",
        help="order the queue by source id, by current-law impact, or by executable unlock potential",
    )
    no_workqueue_p.add_argument(
        "--phrase",
        metavar="TEXT",
        help="restrict the queue to one normalized phrase family",
    )
    no_workqueue_p.add_argument(
        "--override-state",
        choices=["blank", "untracked", "resolved"],
        help="restrict the queue to one override progress state",
    )
    no_workqueue_p.add_argument(
        "--laws-per-source",
        type=int,
        default=5,
        metavar="N",
        help="include up to N top affected laws in each work item",
    )
    no_workqueue_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit listed work items",
    )
    no_workqueue_p.add_argument(
        "--output-dir",
        metavar="DIR",
        help="write summary.json and one JSON packet per work item under DIR",
    )
    no_workqueue_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-candidates ---
    no_commencement_candidates_p = sub.add_parser(
        "no-commencement-candidates",
        help="serialized Norway commencement candidate artifact for one source",
    )
    no_commencement_candidates_p.add_argument("source_id", metavar="ID", help="Norway source id")
    no_commencement_candidates_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_commencement_candidates_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_candidates_p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="limit listed candidates",
    )
    no_commencement_candidates_p.add_argument(
        "--direct-only",
        action="store_true",
        help="keep only candidates with an exact source-title/id match",
    )
    no_commencement_candidates_p.add_argument(
        "--output",
        metavar="FILE",
        help="write a serialized commencement candidate artifact to FILE",
    )
    no_commencement_candidates_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-backfill ---
    no_commencement_backfill_p = sub.add_parser(
        "no-commencement-backfill",
        help="serialized Norway commencement backfill artifact for unresolved sources",
    )
    no_commencement_backfill_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_commencement_backfill_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_backfill_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before building the backfill plan",
    )
    no_commencement_backfill_p.add_argument(
        "--current-laws-only",
        action="store_true",
        default=True,
        help="restrict the backfill plan to unresolved sources affecting current laws",
    )
    no_commencement_backfill_p.add_argument(
        "--sort",
        default="unlock",
        choices=("source", "impact", "unlock"),
        help="sort unresolved sources before building the backfill plan",
    )
    no_commencement_backfill_p.add_argument(
        "--phrase",
        help="filter unresolved sources to a normalized phrase family",
    )
    no_commencement_backfill_p.add_argument(
        "--override-state",
        dest="override_state",
        help="filter unresolved sources by override progress state",
    )
    no_commencement_backfill_p.add_argument(
        "--laws-per-source",
        type=int,
        default=5,
        metavar="N",
        help="include up to N top affected laws in each backfill item",
    )
    no_commencement_backfill_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="limit listed backfill items",
    )
    no_commencement_backfill_p.add_argument(
        "--output",
        metavar="FILE",
        help="write a serialized commencement backfill artifact to FILE",
    )
    no_commencement_backfill_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-commencement-evidence-plan ---
    no_commencement_evidence_plan_p = sub.add_parser(
        "no-commencement-evidence-plan",
        help="serialized Norway external evidence plan for unresolved contingent cases",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before building the plan",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--current-laws-only",
        action="store_true",
        default=True,
        help="restrict the plan to unresolved sources affecting current laws",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--sort",
        default="unlock",
        choices=("source", "impact", "unlock"),
        help="sort unresolved sources before building the plan",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--phrase",
        help="filter unresolved sources to a normalized phrase family",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--override-state",
        dest="override_state",
        help="filter unresolved sources by override progress state",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--laws-per-source",
        type=int,
        default=5,
        metavar="N",
        help="include up to N top affected laws in each plan item",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="limit listed plan items",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--output",
        metavar="FILE",
        help="write a serialized external evidence plan artifact to FILE",
    )
    no_commencement_evidence_plan_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-progress ---
    no_progress_p = sub.add_parser(
        "no-progress",
        help="compact Norway commencement progress summary by override state",
    )
    no_progress_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_progress_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_progress_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before reporting",
    )
    no_progress_p.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="limit listed blank/untracked work items and phrase groups",
    )
    no_progress_p.add_argument(
        "--output-dir",
        metavar="DIR",
        help="write summary plus blank/untracked packet directories under DIR",
    )
    no_progress_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-verify ---
    no_verify_p = sub.add_parser(
        "no-verify",
        help="compare Norway replay against current consolidated law",
    )
    no_verify_p.add_argument("base_id", help="Norway law id, e.g. no/lov/2005-05-20-28")
    no_verify_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_verify_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_verify_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_verify_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before verifying",
    )
    no_verify_p.add_argument(
        "--verbose",
        action="store_true",
        help="include per-provision divergences",
    )
    no_verify_p.add_argument(
        "--max-divergences",
        type=int,
        metavar="N",
        help="when --verbose is set, include at most N divergences",
    )
    no_verify_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-verify-scan ---
    no_verify_scan_p = sub.add_parser(
        "no-verify-scan",
        help="sample Norway replay-vs-current verification over executable replayable laws",
    )
    no_verify_scan_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_verify_scan_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_verify_scan_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_verify_scan_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before scanning",
    )
    no_verify_scan_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="scan up to N executable fully replayable laws (default: 10)",
    )
    no_verify_scan_p.add_argument(
        "--base-id",
        action="append",
        default=[],
        metavar="LAW_ID",
        help="restrict the scan to one or more Norway law ids",
    )
    no_verify_scan_p.add_argument(
        "--progress",
        action="store_true",
        help="print per-law progress to stderr while scanning",
    )
    no_verify_scan_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-verify-partition ---
    no_verify_partition_p = sub.add_parser(
        "no-verify-partition",
        help="partition Norway verify sample into replay defects vs sparse-source cases",
    )
    no_verify_partition_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_verify_partition_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_verify_partition_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_verify_partition_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before partitioning",
    )
    no_verify_partition_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="scan up to N executable fully replayable laws (default: 10)",
    )
    no_verify_partition_p.add_argument(
        "--base-id",
        action="append",
        default=[],
        metavar="LAW_ID",
        help="restrict the partition to one or more Norway law ids",
    )
    no_verify_partition_p.add_argument(
        "--progress",
        action="store_true",
        help="print per-law progress to stderr while partitioning",
    )
    no_verify_partition_p.add_argument(
        "--output",
        metavar="FILE",
        help="write the partition JSON to FILE",
    )
    no_verify_partition_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- no-verify-workqueue ---
    no_verify_workqueue_p = sub.add_parser(
        "no-verify-workqueue",
        help="list only the actionable Norway replay-defect queue",
    )
    no_verify_workqueue_p.add_argument(
        "--as-of",
        default="2026-03-29",
        metavar="DATE",
        help="comparison date for replay materialization (default: 2026-03-29)",
    )
    no_verify_workqueue_p.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Norway source path: farchive DB or legacy public-archive directory",
    )
    no_verify_workqueue_p.add_argument(
        "--index",
        metavar="FILE",
        help="reuse a prebuilt Norway amendment index JSON",
    )
    no_verify_workqueue_p.add_argument(
        "--commencement",
        metavar="FILE",
        help="apply Norway commencement override JSON before building the queue",
    )
    no_verify_workqueue_p.add_argument(
        "--partition",
        metavar="FILE",
        help="reuse a saved no-verify-partition JSON instead of recomputing",
    )
    no_verify_workqueue_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="scan up to N executable fully replayable laws (default: 10)",
    )
    no_verify_workqueue_p.add_argument(
        "--base-id",
        action="append",
        default=[],
        metavar="LAW_ID",
        help="restrict the queue to one or more Norway law ids",
    )
    no_verify_workqueue_p.add_argument(
        "--progress",
        action="store_true",
        help="print per-law progress to stderr while building the queue",
    )
    no_verify_workqueue_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of plain-text summary",
    )

    # --- diff ---
    diff_p = sub.add_parser(
        "diff",
        help="provision-level diff: replay vs oracle",
        description=(
            "Show which specific sections diverge between the replayed statute and "
            "the consolidated oracle. Gives a per-provision map of where problems are."
        ),
    )
    diff_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    diff_p.add_argument(
        "--address",
        metavar="ADDR",
        help="filter to one provision, e.g. 'section:9a'",
    )
    diff_p.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="only show sections below this similarity (default: 1.0 = imperfect only)",
    )
    diff_p.add_argument(
        "--all",
        dest="all",
        action="store_true",
        help="show all sections including perfect ones",
    )
    diff_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    diff_p.add_argument(
        "--compile-summary",
        dest="compile_summary",
        action="store_true",
        help="show legacy compile summary (canonical/recovered/failed ops, adjudications, strictness)",
    )
    diff_p.add_argument(
        "--text",
        dest="show_text",
        action="store_true",
        help="show full text for diverging sections instead of truncated snippets",
    )
    diff_p.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        help=(
            "run in strict mode (FINLAND_INGESTION_V1 profile): heuristics that the "
            "profile forbids are skipped and recorded as adjudications. "
            "Produces lower score than quirks mode where heuristics are blocked."
        ),
    )

    # --- ops ---
    ops_p = sub.add_parser(
        "ops",
        parents=_P,
        help="list compiled operations with provenance",
        description=(
            "Show all operations compiled during replay, with their source amendment "
            "and target address. Useful for understanding what the pipeline did and "
            "for correlating score changes with specific operations."
        ),
    )
    ops_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    ops_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="filter to ops from one amendment, e.g. 2017/794",
    )
    ops_p.add_argument(
        "--target",
        metavar="ADDR",
        help="filter to ops targeting one provision, e.g. 'section:9a'",
    )
    ops_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    ops_p.add_argument(
        "--oracle-id",
        metavar="ID",
        default="",
        help="[-j ee] explicit EE oracle/consolidated aktViide; used to derive --as-of",
    )
    ops_p.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        default="",
        help="[-j ee] replay cutoff date when no --oracle-id is supplied",
    )
    ops_p.add_argument(
        "--json",
        action="store_true",
        help="[-j ee] emit JSON",
    )
    ops_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="[-j ee] show replay progress on stderr",
    )

    # --- replay-debug ---
    replay_debug_p = sub.add_parser(
        "replay-debug",
        help="inspect replay ops, replay metadata, and event logs with optional source-clause context",
        description=(
            "Replay one Finnish statute, filter compiled ops by source amendment "
            "or target address, and optionally print replay metadata, temporal "
            "event traces, and the source amendment's working clause text."
        ),
    )
    replay_debug_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    replay_debug_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="filter to ops from one amendment, e.g. 2017/794",
    )
    replay_debug_p.add_argument(
        "--target",
        metavar="ADDR",
        help="filter to ops targeting one provision, e.g. 'section:9a'",
    )
    replay_debug_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    replay_debug_p.add_argument(
        "--show-clause-text",
        action="store_true",
        help="print the source amendment's working clause text when --source is set",
    )
    replay_debug_p.add_argument(
        "--show-source-blocks",
        action="store_true",
        help="print normalized source XML block texts (repeals/substitutions/insertions) when --source is set",
    )
    replay_debug_p.add_argument(
        "--show-replay-ops",
        action="store_true",
        help="also print emitted LegalOperation replay ops instead of only compiled op summaries",
    )
    replay_debug_p.add_argument(
        "--show-replay-meta",
        action="store_true",
        help="print filtered replay metadata and replay-side observation lists",
    )
    replay_debug_p.add_argument(
        "--show-temporal-events",
        action="store_true",
        help="print filtered executable temporal events",
    )
    replay_debug_p.add_argument(
        "--show-failed-ops",
        action="store_true",
        help="print filtered failed operations emitted during replay",
    )
    replay_debug_p.add_argument(
        "--show-findings",
        action="store_true",
        help="print filtered typed findings emitted during replay",
    )
    replay_debug_p.add_argument(
        "--contains",
        metavar="TEXT",
        help="substring filter applied to compiled/replay op payloads and metadata",
    )
    replay_debug_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="max replay-meta/event items to print per list (default: 10)",
    )
    replay_debug_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- replay-inspect ---
    replay_inspect_p = sub.add_parser(
        "replay-inspect",
        help="inspect one replayed section subtree, text, and metadata",
        description=(
            "Replay one Finland statute and print the resolved section path, "
            "basic section metadata, a rendered IR subtree, and the section text."
        ),
    )
    replay_inspect_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    replay_inspect_p.add_argument(
        "--section",
        required=True,
        metavar="SECTION",
        help="section filter, e.g. '63 §' or 'chapter:5/section:63'",
    )
    replay_inspect_p.add_argument(
        "--chapter",
        metavar="CHAPTER",
        help="optional chapter scope for ambiguous section labels",
    )
    replay_inspect_p.add_argument(
        "--part",
        metavar="PART",
        help="optional part scope for ambiguous section labels",
    )
    replay_inspect_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    replay_inspect_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- oracle-check ---
    ocheck_p = sub.add_parser(
        "oracle-check",
        help="classify divergences as replay bugs vs oracle issues",
        description=(
            "For each diverging provision, classify as ORACLE_STALE, "
            "EDITORIAL_CONVENTION (oracle issues) vs REPLAY_EXTRA, "
            "REPLAY_MISSING, UNKNOWN (our bugs). "
            "Corpus mode reports adjusted score excluding oracle issues."
        ),
    )
    ocheck_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID, e.g. 2006/1299 (omit for --corpus mode)",
    )
    ocheck_p.add_argument(
        "--corpus",
        action="store_true",
        help="run on standard corpus (batch_test_list.csv, 217 statutes)",
    )
    ocheck_p.add_argument(
        "--corpus-full",
        action="store_true",
        dest="corpus_full",
        help="run on expanded corpus (~3591 statutes)",
    )
    ocheck_p.add_argument(
        "--save",
        action="store_true",
        help="save per-section results to oracle_check_results.csv",
    )
    ocheck_p.add_argument(
        "--db",
        metavar="PATH",
        help="write divergences to SQLite DB (includes replay_text + oracle_text)",
    )
    ocheck_p.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="concurrent statutes (default: cpu_count)",
    )
    ocheck_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )

    # --- gold ---
    gold_p = sub.add_parser(
        "gold",
        help="gold master dataset management",
        description=(
            "Manage the verified gold master dataset. Statutes are tiered: "
            "1=human-verified, 2=oracle-confirmed, 3=oracle-issues-only, "
            "4=unresolved. Use 'promote' to add/re-evaluate, 'verify' to re-check."
        ),
    )
    gold_sub = gold_p.add_subparsers(dest="gold_command", metavar="<subcommand>")

    gold_status_p = gold_sub.add_parser("status", help="show gold master summary")
    gold_status_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show per-statute details for all tiers",
    )

    gold_promote_p = gold_sub.add_parser("promote", help="add or re-evaluate a statute")
    gold_promote_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    gold_promote_p.add_argument(
        "--tier",
        type=int,
        choices=[1, 2, 3, 4],
        help="force tier (default: auto-detected from oracle-check)",
    )
    gold_promote_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
    )

    gold_verify_p = gold_sub.add_parser(
        "verify",
        help="re-verify a statute (or all gold statutes with --strict)",
    )
    gold_verify_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID, e.g. 2009/953 (omit with --strict to check all gold statutes)",
    )
    gold_verify_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
    )
    gold_verify_p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "check strictness for gold statutes via compile_fi. "
            "Reports which gold statutes compile without heuristics. "
            "Saves sentinel list to data/finland/strict_sentinel.csv. "
            "Returns non-zero if a previously-passing strict statute now fails."
        ),
    )

    gold_export_p = gold_sub.add_parser("export", help="dump manifest as JSON")
    gold_export_p.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="write to file instead of stdout",
    )

    # --- delegate ---
    delegate_p = sub.add_parser(
        "delegate",
        help="show delegation clauses in a Finnish statute",
        description=(
            "Extract delegation clauses (asetuksenantovaltuudet) from a statute. "
            "Shows which provisions delegate rulemaking authority to VN/ministerial "
            "decrees or agencies. Use --reverse to show the authority citations of "
            "an asetus (nojalla references to parent law)."
        ),
    )
    delegate_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    delegate_p.add_argument(
        "--type",
        metavar="TYPE",
        help="filter by type (comma-separated): VN_ASETUS,MIN_ASETUS,AGENCY,...",
    )
    delegate_p.add_argument(
        "--reverse",
        action="store_true",
        help="reverse mode: show nojalla authority refs from an asetus preamble",
    )
    delegate_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show surrounding text for each match",
    )

    # --- cite ---
    cite_p = sub.add_parser(
        "cite",
        help="show cross-reference edges for a Finnish statute",
        description=(
            "Extract cross-reference edges: CITES (inline body refs), "
            "REPEALS, ISSUED_UNDER, ISSUES, and EU cross-jurisdiction refs "
            "(target_statute_id='eu/TYPE/YEAR/NUMBER'). "
            "Use --type to filter to specific edge types."
        ),
    )
    cite_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    cite_p.add_argument(
        "--type",
        metavar="TYPE",
        help="filter by type (comma-separated): CITES,REPEALS,ISSUED_UNDER,ISSUES",
    )
    cite_p.add_argument(
        "--no-eu",
        action="store_true",
        help="suppress EU cross-jurisdiction references (default: included)",
    )

    # --- timeline ---
    timeline_p = sub.add_parser(
        "timeline",
        help="temporal versioning: provision lineage and PIT materialization",
        description=(
            "Build ProvisionTimelines from Finnish statute replay (Phase 7). "
            "Supports: summary, provision lineage, PIT materialization, and JSON export."
        ),
    )
    timeline_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    timeline_p.add_argument(
        "--list",
        action="store_true",
        help="list all addressable provisions with version counts",
    )
    timeline_p.add_argument(
        "--provision",
        metavar="ADDR",
        help="show version lineage of one provision, e.g. 'section:4' or 'chapter:1/section:4'",
    )
    timeline_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="materialize statute at a point in time, e.g. '2015-06-01'",
    )
    timeline_p.add_argument(
        "--export",
        metavar="FILE",
        help="export all timelines as JSON",
    )
    timeline_p.add_argument(
        "--query-type",
        metavar="TYPE",
        default="governing",
        choices=["governing", "in_force"],
        help=(
            "PIT query semantics for --as-of: "
            "'governing' (Q2, default) includes retroactive amendments; "
            "'in_force' (Q1) returns only what was enacted by that date"
        ),
    )

    # --- export ---
    export_p = sub.add_parser(
        "export",
        help="batch export statute graph to Neo4j CSV or JSON-LD",
        description=(
            "Export the compiled statute graph from local ZIPs (no replay needed). "
            "Produces statute node table + amendment/delegation/citation edge tables."
        ),
    )
    export_p.add_argument(
        "--neo4j",
        metavar="OUTPUT_DIR",
        help="write Neo4j bulk import CSVs to this directory",
    )
    export_p.add_argument(
        "--jsonld",
        metavar="OUTPUT_FILE",
        help="write JSON-LD statute graph to this file",
    )
    export_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="corpus CSV (default: .tmp/batch_test_list.csv)",
    )
    export_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="process only first N statutes (for testing)",
    )
    export_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print progress during export",
    )

    # --- graph ---
    graph_p = sub.add_parser(
        "graph",
        help="cross-statute graph queries (CorpusGraph, Phase 9.4/9.5)",
        description=(
            "Build a CorpusGraph from the standard corpus and run cross-statute queries. "
            "Defaults to lightweight mode (no replay, ~seconds). "
            "Use --with-timelines for temporal filtering (slower). "
            "Available queries: --reverse-cites, --affecting-acts, --delegates, --silent-breakage."
        ),
    )
    graph_p.add_argument("statute_id", help="target statute ID, e.g. 2009/953")
    graph_p.add_argument(
        "--reverse-cites",
        action="store_true",
        help="show all statutes (within corpus) that cite statute_id",
    )
    graph_p.add_argument(
        "--affecting-acts",
        action="store_true",
        help="show acts that have amended statute_id",
    )
    graph_p.add_argument(
        "--delegates",
        action="store_true",
        help="show delegation clauses in statute_id",
    )
    graph_p.add_argument(
        "--silent-breakage",
        action="store_true",
        help="show provisions that cite statute_id (may have been silently affected)",
    )
    graph_p.add_argument(
        "--provision",
        metavar="FRAG",
        help="filter --silent-breakage to provisions citing this section fragment (e.g. 'section/3')",
    )
    graph_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="ISO date for temporal filter in --silent-breakage (requires --with-timelines)",
    )
    graph_p.add_argument(
        "--with-timelines",
        action="store_true",
        help="load full provision timelines (enables --as-of filtering, much slower)",
    )
    graph_p.add_argument(
        "--corpus",
        metavar="CSV",
        help="override corpus CSV (default: .tmp/batch_test_list.csv)",
    )
    graph_p.add_argument(
        "--concurrency",
        type=int,
        default=8,
        metavar="N",
        help="build concurrency (default: 8)",
    )

    # --- build ---
    build_p = sub.add_parser(
        "build",
        help="compile the legal graph to a persistent artifact directory",
        description=(
            "Build a persistent corpus graph artifact from Finnish ZIP or Norwegian Lovdata archive. "
            "Lightweight (no replay) by default; add --with-timelines for provision-level history."
        ),
    )
    build_p.add_argument(
        "--corpus",
        metavar="CSV",
        help="build from a statute-ID CSV (format: N,YYYY/NNN); mutually exclusive with --full",
    )
    build_p.add_argument(
        "--full",
        action="store_true",
        help="build all statutes from the Finnish ZIP (~59K, lightweight only)",
    )
    build_p.add_argument(
        "--output",
        metavar="DIR",
        required=True,
        help="output directory (created if needed)",
    )
    build_p.add_argument(
        "--jurisdiction",
        metavar="JURI",
        default="fi",
        choices=["fi", "no"],
        help="jurisdiction: 'fi' (Finnish ZIP, default) or 'no' (Norwegian Lovdata archive)",
    )
    build_p.add_argument(
        "--input",
        metavar="FILE",
        help="input archive path (required for --jurisdiction no)",
    )
    build_p.add_argument(
        "--amendment-archive",
        dest="amendment_archives",
        action="append",
        metavar="FILE",
        help="additional Lovtidend amendment archive path (repeatable; --jurisdiction no only)",
    )
    build_p.add_argument(
        "--with-timelines",
        action="store_true",
        help="also replay amendments and store provision timelines (slow; Finnish only)",
    )
    build_p.add_argument(
        "--concurrency",
        type=int,
        default=16,
        metavar="N",
        help="build concurrency for Finnish lightweight build (default: 16)",
    )
    build_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-statute progress",
    )

    # --- query ---
    from lawvm.tools.query import register_cli as _register_query

    _register_query(sub)

    # --- oracle-classify ---
    oc_p = sub.add_parser(
        "oracle-classify",
        help="classify oracle quality for a corpus of statutes",
        description=(
            "Reads the consolidated ZIP and classifies each statute oracle as "
            "FULL/PARTIAL/REPEALED/EMPTY/ABSENT/MISSING. Writes a CSV."
        ),
    )
    oc_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="corpus CSV to classify (default: full consolidated ZIP)",
    )
    oc_p.add_argument(
        "--output",
        metavar="CSV_PATH",
        help="output CSV path (default: print summary only)",
    )

    # --- bench-curate ---
    bc_p = sub.add_parser(
        "bench-curate",
        help="partition Finland bench corpus into core/suspect/notruth/pending",
        description=(
            "Build benchmark corpus partitions so the main bench measures only "
            "commensurable oracle states. `core` = usable truth and no known "
            "oracle-version mismatch; `suspect` = version-frontier mismatch; "
            "`notruth` = no commensurable oracle; `pending` = operationally unresolved."
        ),
    )
    bc_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="input corpus CSV (default: data/finland/bench_corpus.csv)",
    )
    bc_p.add_argument(
        "--run",
        metavar="LABEL_OR_PATH",
        action="append",
        help=(
            "bench run label or CSV path to use for NO_TRUTH / operational status seeding; "
            "may be repeated, later runs override earlier statuses"
        ),
    )
    bc_p.add_argument(
        "--strict-run",
        metavar="LABEL_OR_PATH",
        action="append",
        help=(
            "strict run label or CSV path to use for source-pathology suspect seeding; "
            "may be repeated, later runs override earlier signals"
        ),
    )
    bc_p.add_argument(
        "--output-dir",
        metavar="DIR",
        help="output directory for bench_core.csv etc. (default: data/finland)",
    )
    bc_p.add_argument(
        "--oracle-suspect-check",
        choices=["off", "cache-only"],
        default="cache-only",
        help="whether to enrich the partition with oracle-version suspect checks (default: cache-only)",
    )

    # --- bench-hydrate ---
    bh_p = sub.add_parser(
        "bench-hydrate",
        help="serially hydrate source/oracle cache for a benchmark corpus",
        description=(
            "Run serial source/oracle warm passes for a corpus so later benches "
            "read from SQLite instead of making live fetches."
        ),
    )
    bh_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="input corpus CSV (default: data/finland/bench_pending.csv)",
    )
    bh_p.add_argument(
        "--passes",
        type=int,
        default=3,
        help="maximum serial hydrate passes (default: 3)",
    )

    # --- census ---
    census_p = sub.add_parser(
        "census",
        help="run Tier 1 corpus census queries against a pre-built artifact",
        description=(
            "Run census 1.1–1.5 against a lawvm build artifact and write CSVs. "
            "Add --report to generate a Markdown census report."
        ),
    )
    census_p.add_argument(
        "--graph",
        metavar="DIR",
        required=True,
        help="artifact directory (produced by lawvm build)",
    )
    census_p.add_argument(
        "--output",
        metavar="DIR",
        required=True,
        help="directory for CSV and report output (created if absent)",
    )
    census_p.add_argument(
        "--only",
        metavar="LIST",
        help="comma-separated census IDs to run (default: 1.1,1.2,1.3,1.4,1.5)",
    )
    census_p.add_argument(
        "--report",
        action="store_true",
        help="also generate census_report.md in the output directory",
    )

    # --- coverage ---
    coverage_p = sub.add_parser(
        "coverage",
        help="corpus coverage audit — 'Is The Law Complete?'",
        description=(
            "Scan consolidated corpus and report coverage gaps per statute: "
            "contentAbsent (repealed/undigitized), GIF images (tables as scans), "
            "corrigendum PDFs (legally binding errata), annexed PDFs. "
            "Fast scan (default) uses path enumeration only (~1s). "
            "--deep also reads XMLs to detect contentAbsent (~60s, cached)."
        ),
    )
    coverage_p.add_argument(
        "statute_id",
        nargs="?",
        help="single statute ID (e.g. 2007/26) — full breakdown",
    )
    coverage_p.add_argument(
        "--deep",
        action="store_true",
        help="read XMLs to detect contentAbsent (slow first run, cached thereafter)",
    )
    coverage_p.add_argument(
        "--rebuild",
        action="store_true",
        help="force rebuild contentAbsent cache (implies --deep)",
    )
    coverage_p.add_argument(
        "--gaps",
        action="store_true",
        help="only show statutes with non-cosmetic coverage gaps",
    )
    coverage_p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )

    # --- corrigendum ---
    from lawvm.tools.corrigendum import register_cli as _register_corrigendum

    _register_corrigendum(sub)

    # --- faults ---
    faults_p = sub.add_parser(
        "faults",
        help="fault evidence builder for Finlex divergences (from oracle-check --db)",
        description=(
            "Generate independently verifiable evidence for each Finlex divergence. "
            "Reads divergences.db produced by `lawvm oracle-check --db`. "
            "Subcommands: list, evidence, export, summary."
        ),
    )
    faults_sub = faults_p.add_subparsers(dest="faults_command", metavar="<subcommand>")

    faults_list_p = faults_sub.add_parser(
        "list",
        help="list faults with severity and fault type",
    )
    faults_list_p.add_argument(
        "--min-severity",
        dest="min_severity",
        type=int,
        default=1,
        metavar="N",
        help="minimum severity level 1-3 (default: 1 = all faults)",
    )
    faults_list_p.add_argument(
        "--diagnosis",
        metavar="DIAG",
        help="filter to one diagnosis (e.g. REPLAY_MISSING)",
    )
    faults_list_p.add_argument(
        "--db",
        metavar="PATH",
        help="path to divergences.db (default: .tmp/divergences.db)",
    )

    faults_evidence_p = faults_sub.add_parser(
        "evidence",
        help="generate 4-step proof JSON for one statute (or one section)",
    )
    faults_evidence_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    faults_evidence_p.add_argument(
        "--section",
        metavar="SECTION",
        help="filter to one section (e.g. '3')",
    )
    faults_evidence_p.add_argument(
        "--db",
        metavar="PATH",
        help="path to divergences.db (default: .tmp/divergences.db)",
    )

    faults_export_p = faults_sub.add_parser(
        "export",
        help="export all faults as JSONL",
    )
    faults_export_p.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        required=True,
        help="output JSONL file path",
    )
    faults_export_p.add_argument(
        "--min-severity",
        dest="min_severity",
        type=int,
        default=1,
        metavar="N",
        help="minimum severity level 1-3 (default: 1 = all faults)",
    )
    faults_export_p.add_argument(
        "--diagnosis",
        metavar="DIAG",
        help="filter to one diagnosis (e.g. REPLAY_MISSING)",
    )
    faults_export_p.add_argument(
        "--db",
        metavar="PATH",
        help="path to divergences.db (default: .tmp/divergences.db)",
    )
    faults_export_p.add_argument(
        "--finlex-only",
        action="store_true",
        dest="finlex_only",
        help="only export cases where Finlex is behind (REPLAY_EXTRA + EXTRA), "
        "excluding LawVM replay bugs (REPLAY_MISSING + MISSING)",
    )

    faults_summary_p = faults_sub.add_parser(
        "summary",
        help="aggregate fault statistics",
    )
    faults_summary_p.add_argument(
        "--db",
        metavar="PATH",
        help="path to divergences.db (default: .tmp/divergences.db)",
    )

    # --- failures ---
    failures_p = sub.add_parser(
        "failures",
        help="analyse replay FailedOp records across bench corpus",
        description=(
            "Replay statutes and collect structured FailedOp records. "
            "Shows failure reason distribution, description patterns, "
            "and affected statutes. Useful for accuracy grinding."
        ),
    )
    failures_p.add_argument(
        "statute_id",
        nargs="?",
        default=None,
        help="single statute ID to analyse (default: full bench corpus)",
    )
    failures_p.add_argument(
        "--pattern",
        metavar="REGEX",
        help="filter failures by description regex (e.g. 'kohta', 'mom')",
    )
    failures_p.add_argument(
        "--top",
        type=int,
        default=15,
        metavar="N",
        help="show top N entries in each category (default: 15)",
    )
    failures_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print progress to stderr",
    )
    failures_p.add_argument(
        "--detail",
        action="store_true",
        help="categorize each failure by root cause (kohta_no_paras / kohta_label_gap / mom_oor / renumber / other)",
    )
    failures_p.add_argument(
        "--from-bench",
        metavar="LABEL",
        help="only replay imperfect statutes from a bench run (reads cache if available)",
    )
    failures_p.add_argument(
        "--parallel",
        type=int,
        default=8,
        metavar="N",
        help="parallel replay workers (default: 8)",
    )
    failures_p.add_argument(
        "--save-cache",
        metavar="LABEL",
        help="save failures to a cache file under data/bench_runs/ (auto with --from-bench)",
    )

    # --- audit ---
    audit_p = sub.add_parser(
        "audit",
        help="cross-format consistency audit (oracle staleness detection)",
        description=(
            "Detect cases where Finlex XML data sources are stale relative to the HTML "
            "website and LawVM replay. Subcommands: formats, staleness, html."
        ),
    )
    audit_sub = audit_p.add_subparsers(dest="audit_cmd", metavar="<subcommand>")

    audit_formats_p = audit_sub.add_parser(
        "formats",
        help="full cross-format comparison for one statute",
        description=(
            "Compare section counts across all data sources: original XML (source corpus), "
            "consolidated XML (cons.zip), API XML, HTML website, and LawVM replay. "
            "Diagnoses oracle staleness where XML is missing sections present in HTML/replay."
        ),
    )
    audit_formats_p.add_argument("statute_id", help="statute ID, e.g. 2018/1121")
    audit_formats_p.add_argument(
        "--no-api",
        dest="no_api",
        action="store_true",
        help="skip API fetch (opendata.finlex.fi)",
    )
    audit_formats_p.add_argument(
        "--no-html",
        dest="no_html",
        action="store_true",
        help="skip HTML fetch (finlex.fi website)",
    )

    audit_staleness_p = audit_sub.add_parser(
        "staleness",
        help="corpus-wide staleness scan (ZIP-only, no HTTP calls)",
        description=(
            "For every statute in consolidated corpus, compare consolidated vs "
            "original section count. Flag statutes with amendments post-2020 where "
            "the consolidated XML section count equals the original (XML not updated). "
            "Writes .tmp/audit_staleness.csv."
        ),
    )
    audit_staleness_p.add_argument(
        "--graph",
        metavar="DIR",
        help="corpus graph artifact directory (for amendments.json; default: .tmp/corpus_graph_full/)",
    )
    audit_staleness_p.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="output CSV path (default: .tmp/audit_staleness.csv)",
    )
    audit_staleness_p.add_argument(
        "--top",
        type=int,
        metavar="N",
        help="show top N stale statutes in terminal (default: 50)",
    )
    audit_staleness_p.add_argument(
        "--min-year",
        dest="min_year",
        type=int,
        default=2020,
        metavar="YEAR",
        help="minimum latest-amendment year to flag as stale (default: 2020)",
    )

    audit_body_pairing_p = audit_sub.add_parser(
        "body-pairing",
        help="body-driven pairing analysis: detect foreign/unmatched body units",
        description=(
            "Run body pairing analysis on amendment body content vs johtolause claims. "
            "Detects body sections that belong to a different statute (foreign), have "
            "no matching clause claim (unmatched), or are blocked by REPEAL claims."
        ),
    )
    audit_body_pairing_p.add_argument(
        "statute_ids",
        nargs="*",
        help="one or more statute IDs, e.g. 2018/1121 1994/1205",
    )
    audit_body_pairing_p.add_argument(
        "--from-file",
        dest="from_file",
        metavar="FILE",
        help="text file with one statute ID per line",
    )
    audit_body_pairing_p.add_argument(
        "--anomalies-only",
        action="store_true",
        help="only show amendments with findings (foreign, unmatched, or repeal-blocked)",
    )
    audit_body_pairing_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON output",
    )
    audit_body_pairing_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="max statutes to process (0 = all)",
    )

    audit_html_p = audit_sub.add_parser(
        "html",
        help="fetch live HTML and compare vs XML for one statute (or a list)",
        description=(
            "Fetch finlex.fi HTML, extract section numbers, and compare against "
            "the consolidated XML. Reports sections present in HTML but absent in XML. "
            "Use --from-file for batch processing."
        ),
    )
    audit_html_p.add_argument(
        "statute_ids",
        nargs="*",
        help="one or more statute IDs, e.g. 2018/1121 1994/1205",
    )
    audit_html_p.add_argument(
        "--from-file",
        dest="from_file",
        metavar="FILE",
        help="text file with one statute ID per line",
    )
    audit_html_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable text",
    )
    audit_html_p.add_argument(
        "--exclude-range-headings",
        action="store_true",
        help="skip statutes whose HTML contains merged/range presentation headings",
    )

    # --- bilingual ---
    bilingual_p = sub.add_parser(
        "bilingual",
        help="structural comparison: Finnish vs Swedish statute versions",
        description=(
            "Finnish legislation is constitutionally bilingual — fin and swe versions "
            "must be structurally isomorphic (same sections, chapters, parts). "
            "Reads source XMLs from source corpus (or Farchive if swe is imported). "
            "Divergences are bug signals in the source XML or pipeline."
        ),
    )
    bilingual_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID to check (e.g. 2009/953); omit with --all for corpus scan",
    )
    bilingual_p.add_argument(
        "--all",
        action="store_true",
        help="scan entire corpus and print summary",
    )
    bilingual_p.add_argument(
        "--divergences",
        action="store_true",
        help="with --all: print full detail for each diverged statute",
    )
    bilingual_p.add_argument(
        "--archive-db",
        metavar="PATH",
        dest="archive_db",
        help="path to Farchive DB (uses archive if swe has been imported)",
    )

    # --- uk-replay ---
    uk_replay_p = sub.add_parser(
        "uk-replay",
        help="UK amendment replay with timeline integration",
        description=(
            "Replay UK legislation amendments (from effects feeds) against the "
            "archive-backed enacted base statute, compare against the archive-backed "
            "oracle (current or PIT-dated when present), compile provision timelines, "
            "and report EID similarity."
        ),
    )
    uk_replay_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/1998/42",
    )
    uk_replay_p.add_argument(
        "--pit-date",
        dest="pit_date",
        metavar="YYYY-MM-DD",
        help="point-in-time date for replay and oracle comparison",
    )
    uk_replay_p.add_argument(
        "--enacted-only",
        dest="enacted_only",
        action="store_true",
        help="compare enacted vs enacted (baseline, no replay)",
    )
    uk_replay_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print detailed operation and pipeline info",
    )
    uk_replay_p.add_argument(
        "--fetch-missing",
        dest="fetch_missing",
        action="store_true",
        help="fetch missing affecting act XMLs into the archive before replaying",
    )
    uk_replay_p.add_argument(
        "--include-enacted-affecting",
        action="store_true",
        help=(
            "with --fetch-missing, also fetch /enacted/data.xml for cached or "
            "newly fetched affecting acts"
        ),
    )
    uk_replay_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive); required because deprecated on-disk XML is no longer used",
    )
    uk_replay_p.add_argument(
        "--timeline",
        action="store_true",
        help=(
            "compile ops-first timelines via compile_timelines() and print a "
            "per-provision version-count summary (default: states-first via "
            "ingest_uk_snapshots)"
        ),
    )
    uk_replay_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    uk_replay_p.add_argument(
        "--replay-adjudication-samples",
        nargs="+",
        metavar="KIND",
        help="in text mode, print bounded samples for these replay adjudication kinds",
    )
    uk_replay_p.add_argument(
        "--replay-adjudication-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="maximum replay adjudication samples to print in text mode (default: 5)",
    )
    add_uk_replay_regime_arguments(uk_replay_p, help_prefix="")

    # --- uk-fetch-affecting ---
    uk_fetch_p = sub.add_parser(
        "uk-fetch-affecting",
        help="pre-fetch missing affecting act XMLs into the archive",
        description=(
            "For a given UK statute, inspect its effects feed and download any "
            "affecting act XMLs that are not yet cached in the Farchive DB.  "
            "Run this before uk-replay to maximise the number of ops that can be "
            "compiled from real provision text."
        ),
    )
    uk_fetch_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/1998/42",
    )
    uk_fetch_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_fetch_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print what would be fetched without downloading",
    )
    uk_fetch_p.add_argument(
        "--include-enacted-affecting",
        action="store_true",
        help="also fetch /enacted/data.xml for cached or newly fetched affecting acts",
    )
    uk_fetch_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print a line for every affecting act checked",
    )
    uk_fetch_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON including acquisition event rows",
    )

    # --- uk-effect ---
    uk_effect_p = sub.add_parser(
        "uk-effect",
        help="inspect one UK effects-feed row end to end",
        description=(
            "Archive-backed UK effect inspection. Shows one effects-feed row, "
            "the extracted affecting-act source node, and the compiled ops for "
            "a single effect_id."
        ),
    )
    uk_effect_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/2000/26",
    )
    uk_effect_p.add_argument(
        "effect_id",
        help="effect feed row ID, e.g. key-f685836a8260bbac26bd47a7a22cef25",
    )
    uk_effect_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_effect_p.add_argument(
        "--show-text",
        action="store_true",
        help="print the full extracted source text instead of only a short snippet",
    )
    uk_effect_p.add_argument(
        "--show-payload",
        action="store_true",
        help="print a compact tree view of each compiled payload",
    )
    uk_effect_p.add_argument(
        "--applicability-mode",
        dest="uk_applicability_mode",
        choices=UK_APPLICABILITY_MODE_CHOICES,
        default=None,
        help="UK replay applicability lens for this effect report",
    )
    uk_effect_p.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable single-effect frontier report",
    )

    # --- uk-effects ---
    uk_effects_p = sub.add_parser(
        "uk-effects",
        help="list/search UK effects-feed rows for one statute",
        description=(
            "Archive-backed UK effect listing. Useful for localizing the next "
            "replay family before inspecting one row with uk-effect."
        ),
    )
    uk_effects_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/2000/22",
    )
    uk_effects_p.add_argument(
        "--affected-contains",
        metavar="TEXT",
        help="case-insensitive substring filter on affected provisions",
    )
    uk_effects_p.add_argument(
        "--affecting-contains",
        metavar="TEXT",
        help="case-insensitive substring filter on affecting provisions",
    )
    uk_effects_p.add_argument(
        "--effect-type-contains",
        metavar="TEXT",
        help="case-insensitive substring filter on effect type",
    )
    uk_effects_p.add_argument(
        "--source-pathology",
        metavar="CLASS",
        help="only show rows with this typed source-pathology class; use __none__ for clean source",
    )
    uk_effects_p.add_argument(
        "--lowering-rule",
        metavar="RULE_ID",
        help="only show rows carrying this lowering rejection rule ID",
    )
    uk_effects_p.add_argument(
        "--source-acquisition-rule",
        metavar="RULE_ID",
        help="only show rows carrying this source-acquisition rejection rule ID",
    )
    uk_effects_p.add_argument(
        "--manual-compile-status",
        metavar="STATUS",
        help=(
            "only show rows with this manual compile frontier status "
            "(for example manual_compile_candidate)"
        ),
    )
    uk_effects_p.add_argument(
        "--manual-compile-rule",
        metavar="RULE_ID",
        help="only show rows with this manual compile frontier rule ID",
    )
    uk_effects_p.add_argument(
        "--applied-only",
        action="store_true",
        help="only show applied effects",
    )
    uk_effects_p.add_argument(
        "--structural-only",
        action="store_true",
        help="only show structural effects",
    )
    add_uk_replay_regime_arguments(
        uk_effects_p,
        help_prefix="",
        include_metadata_only_effects=True,
    )
    uk_effects_p.add_argument(
        "--candidate-only",
        action="store_true",
        help="only show rows whose typed source and compare classifications remain replay candidates",
    )
    uk_effects_p.add_argument(
        "--non-candidate-only",
        action="store_true",
        help="only show rows defeated by typed source or compare classification",
    )
    uk_effects_p.add_argument(
        "--limit",
        type=int,
        help="maximum number of rows to print after filtering",
    )
    uk_effects_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_effects_p.add_argument(
        "--summary-only",
        action="store_true",
        help="print only aggregate UK effect classification counts",
    )
    uk_effects_p.add_argument(
        "--evidence-jsonl",
        metavar="PATH",
        help=(
            "write selected UK effect diagnostic rows as JSONL, suitable as a "
            "manual-compile work queue"
        ),
    )
    uk_effects_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable UK effect classification rows and summary",
    )

    # --- uk-eids ---
    uk_eids_p = sub.add_parser(
        "uk-eids",
        help="inspect nearby UK EIDs/text by prefix",
        description=(
            "Archive-backed UK EID inspector. Useful when a row looks like a "
            "compare-shape or legacy-label issue and you want to inspect nearby "
            "base/oracle EIDs without ad hoc Python."
        ),
    )
    uk_eids_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/2000/23",
    )
    uk_eids_p.add_argument(
        "--prefix",
        required=True,
        metavar="EID_PREFIX",
        help="EID prefix to inspect, e.g. section-72 or schedule-1-part-a1",
    )
    uk_eids_p.add_argument(
        "--side",
        choices=["base", "oracle", "both"],
        default="both",
        help="which archive-backed side to inspect (default: both)",
    )
    uk_eids_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=40,
        help="maximum number of matching EIDs to print per side (default: 40)",
    )
    uk_eids_p.add_argument(
        "--show-text",
        action="store_true",
        help="print a compact text snippet for each matched EID",
    )
    uk_eids_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable UK EID match rows and side summaries",
    )
    uk_eids_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )

    # --- eu-replay ---
    eu_replay_p = sub.add_parser(
        "eu-replay",
        help="replay one EU CELEX act and report adjudication signals",
        description=(
            "Fetches the CELEX baseline, discovers affecting acts from "
            "Cellar metadata, applies available operations, and prints a "
            "summary including replay warnings and duplicated-text lint hits."
        ),
    )
    eu_replay_p.add_argument("celex", help="EU CELEX identifier, e.g. 32000R0000")
    eu_replay_p.add_argument(
        "--pit-date",
        dest="pit_date",
        metavar="YYYY-MM-DD",
        help="PIT cutoff date for timeline materialization",
    )
    eu_replay_p.add_argument(
        "--cache-dir",
        default=".cache/eu_replay",
        help="cache root used by EU replay pipeline",
    )
    eu_replay_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON output (deprecated: equivalent to --format=json).",
    )
    eu_replay_p.add_argument(
        "--format",
        choices=("text", "json", "markdown"),
        default="text",
        help="output format: text (default), json, or markdown",
    )

    # --- eu-reul ---
    eu_reul_p = sub.add_parser(
        "eu-reul",
        help="inspect EU retained-law bridge mapping and resolution",
        description=(
            "Utility bridge for EU CELEX references and retained-law URIs. "
            "`map` converts CELEX + relative EU path to a UK REUL-like EID. "
            "`resolve` validates retained-law:// URIs against a local EU parsed IR."
        ),
    )
    eu_reul_sub = eu_reul_p.add_subparsers(
        dest="eu_reul_command",
        metavar="<command>",
        required=True,
    )

    eu_reul_map_p = eu_reul_sub.add_parser(
        "map",
        help="map CELEX + EU path to UK retained-law frontend EID",
    )
    eu_reul_map_p.add_argument("celex", help="EU CELEX id, e.g. 32016R0679")
    eu_reul_map_p.add_argument(
        "eu_path",
        help="EU path from REUL source, e.g. art/1/para/2",
    )
    eu_reul_map_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with parsed fields instead of plain text",
    )

    eu_reul_resolve_p = eu_reul_sub.add_parser(
        "resolve",
        help="resolve retained-law URI against parsed EU IR",
    )
    eu_reul_resolve_p.add_argument(
        "uri",
        help="retained-law URI, e.g. retained-law://celex/32016R0679/article/1",
    )
    eu_reul_resolve_p.add_argument(
        "statute_xml",
        help="path to local EU regulation XML used for REUL resolution",
    )
    eu_reul_resolve_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with the resolved payload",
    )

    # --- uk-candidates ---
    uk_candidates_p = sub.add_parser(
        "uk-candidates",
        help="candidate-aware UK frontier triage from a saved bench run",
        description=(
            "Read a saved UK bench run and summarize the worst core rows using the "
            "same typed source/compare gating as uk-effect/uk-effects."
        ),
    )
    uk_candidates_p.add_argument(
        "--label",
        required=True,
        metavar="LABEL",
        help="saved UK bench run label, e.g. uk_typed_frontier_20260329",
    )
    uk_candidates_p.add_argument(
        "--top",
        type=int,
        default=15,
        metavar="N",
        help="inspect the worst N core rows from the saved run (default: 15)",
    )
    uk_candidates_p.add_argument(
        "--types",
        nargs="+",
        metavar="TYPE",
        help="restrict to act types, e.g. ukpga asp asc nia",
    )
    uk_candidates_p.add_argument(
        "--min-year",
        dest="min_year",
        type=int,
        metavar="YEAR",
        help="restrict to statutes from YEAR onward",
    )
    uk_candidates_p.add_argument(
        "--max-year",
        dest="max_year",
        type=int,
        metavar="YEAR",
        help="restrict to statutes up to YEAR",
    )
    uk_candidates_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_candidates_p.add_argument(
        "--fast",
        action="store_true",
        help="rank the saved run without archive-backed per-effect summaries",
    )
    uk_candidates_p.add_argument(
        "--effect-budget",
        type=int,
        metavar="N",
        help=(
            "maximum replay-applicable effects per statute to inspect in "
            "archive-backed mode"
        ),
    )
    uk_candidates_p.add_argument(
        "--residual-budget",
        type=int,
        metavar="N",
        help="maximum frontier rows to run archive-backed replay/oracle residual analysis for",
    )
    uk_candidates_p.add_argument(
        "--score-mode",
        choices=("auto", "replay", "replay_commencement"),
        default="auto",
        help="which saved score to rank by (default: auto)",
    )
    uk_candidates_p.add_argument(
        "--residual-only",
        action="store_true",
        help="show only statutes with nonzero residual-driving candidate rows",
    )
    uk_candidates_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable UK candidate/residual triage rows",
    )
    uk_candidates_p.add_argument(
        "--summary-only",
        action="store_true",
        help="with --json, omit per-statute candidate rows and emit only aggregate triage counts",
    )
    uk_candidates_p.add_argument(
        "--manual-compile-evidence-jsonl",
        metavar="PATH",
        help=(
            "archive-backed mode only: write all inspected manual_compile_candidate "
            "effect rows as source-witnessed JSONL work items"
        ),
    )
    uk_candidates_p.add_argument(
        "--replay-adjudication-kind",
        nargs="+",
        metavar="KIND",
        help=(
            "restrict saved-run frontier rows to statutes with one of these replay "
            "adjudication kinds and include bounded samples"
        ),
    )
    uk_candidates_p.add_argument(
        "--replay-adjudication-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="maximum replay adjudication samples to include per emitted statute (default: 5)",
    )

    # --- disagreement ---
    disagree_p = sub.add_parser(
        "disagreement",
        help="mine pipeline captures for high-leverage fix targets",
        description=(
            "Two-phase tool for disagreement mining. "
            "--populate: run captures for the top-N worst-scoring statutes in a "
            "labeled bench run and save JSON bundles to data/disagreement/<label>/. "
            "--analyze: scan saved captures and detect EXTRACTION_MISS, "
            "ADDRESS_MISMATCH, SPARSE_PAYLOAD, and PEG_UNDER_EXTRACT patterns, "
            "then emit a ranked worklist."
        ),
    )
    disagree_p.add_argument(
        "--label",
        required=True,
        metavar="LABEL",
        help="bench run label to read worst statutes from (e.g. disagree_v1)",
    )
    disagree_p.add_argument(
        "--populate",
        action="store_true",
        help="run build_capture() for top-N worst statutes and save JSON bundles",
    )
    disagree_p.add_argument(
        "--analyze",
        action="store_true",
        help="scan saved captures and produce ranked worklist",
    )
    disagree_p.add_argument(
        "--top",
        type=int,
        default=50,
        metavar="N",
        help="number of worst statutes to capture (default: 50)",
    )
    disagree_p.add_argument(
        "--force",
        action="store_true",
        help="re-capture even if JSON already exists (default: skip cached)",
    )
    disagree_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print each finding during --analyze",
    )

    # --- frontier ---
    from lawvm.tools.frontier import register_cli as _register_frontier

    _register_frontier(sub)

    # --- strict-report ---
    strict_p = sub.add_parser(
        "strict-report",
        help="strict-path compilation report — single statute or corpus-wide",
        description=(
            "Single-statute: compile one statute and show canonical/recovered/failed ops, "
            "heuristics fired, and source completeness. "
            "Corpus-wide: run compile_fi across the bench corpus and report strict pass "
            "rate, per-quirk frequency, source-incomplete rate, and strict-vs-canonical "
            "correlation. "
            "Usage: lawvm strict-report 2009/953  "
            "or:    lawvm strict-report --parallel 4 --label strict_v1  "
            "or:    lawvm strict-report --show strict_v1"
        ),
    )
    strict_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID for single-statute mode (e.g. 2009/953); omit for corpus-wide mode",
    )
    strict_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for single-statute mode (default: finlex_oracle)",
    )
    strict_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show per-op details (single-statute mode)",
    )
    strict_p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit JSON instead of text (single-statute mode)",
    )
    strict_p.add_argument(
        "--label",
        metavar="LABEL",
        help="corpus mode: tag this run (e.g. strict_v1); also triggers corpus mode",
    )
    strict_p.add_argument(
        "--show",
        metavar="LABEL",
        help="corpus mode: display a previously saved run without re-running",
    )
    strict_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="corpus mode: path to corpus CSV (default: data/finland/bench_corpus.csv)",
    )
    strict_p.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help="corpus mode: number of parallel workers (default: cpu_count)",
    )
    strict_p.add_argument(
        "--facade",
        dest="facade",
        action="store_true",
        help=(
            "single-statute mode: also show CompileFacade summary "
            "(observations, temporal_events, quirks_used, source_completeness_issues, "
            "strictness) built from the replay PhaseResult"
        ),
    )

    # --- freshness ---
    freshness_p = sub.add_parser(
        "freshness",
        help="freshness audit: compare ZIP vs API vs HTML oracle section counts",
        description=(
            "For each statute in the bench corpus (or a sample), compare section "
            "counts from three sources: local corpus oracle (fast), PIT API XML "
            "(network), and the HTML website (ground truth). "
            "Flags statutes where the corpus oracle is stale relative to the website. "
            "Saves a CSV to data/freshness_reports/. "
            "Usage: lawvm freshness --sample 50 --label fresh_v1"
        ),
    )
    freshness_p.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="audit a sample of N statutes (default: 50; prefers source_incomplete set)",
    )
    freshness_p.add_argument(
        "--corpus",
        action="store_true",
        help="audit the full bench corpus (slow — all statutes)",
    )
    freshness_p.add_argument(
        "--label",
        metavar="LABEL",
        default="fresh_v1",
        help="label for this run (used in output CSV filename, default: fresh_v1)",
    )
    freshness_p.add_argument(
        "--no-api",
        dest="no_api",
        action="store_true",
        help="skip PIT API checks (faster, ZIP-only + HTML)",
    )
    freshness_p.add_argument(
        "--no-html",
        dest="no_html",
        action="store_true",
        help="skip HTML website checks (faster, ZIP + API only)",
    )
    freshness_p.add_argument(
        "--corpus-path",
        dest="corpus_path",
        metavar="CSV",
        help="override corpus CSV (default: data/finland/bench_corpus.csv)",
    )
    freshness_p.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="parallel workers for ZIP section counting (default: 4)",
    )
    freshness_p.add_argument(
        "--replay",
        action="store_true",
        help="also run replay_xml() for each statute and compare section counts "
        "(adds replay_sections column; CPU-intensive, sequential, no network)",
    )
    freshness_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-statute progress to stderr",
    )

    # --- step-attribution ---
    sa_p = sub.add_parser(
        "step-attribution",
        help="quantify WHERE accuracy loss happens in the pipeline",
        description=(
            "For each statute, measures loss at four steps: "
            "Extraction (PEG/fallback op count), "
            "Compilation (canonical/recovered/failed split), "
            "Application (FailedOp count), "
            "Materialization (section-by-section oracle comparison). "
            "Single-statute mode prints a step-by-step attribution. "
            "Corpus mode aggregates over the bench corpus."
        ),
    )
    sa_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID, e.g. 1993/1501 (omit for --corpus mode)",
    )
    sa_p.add_argument(
        "--corpus",
        action="store_true",
        help="run over the bench corpus instead of a single statute",
    )
    sa_p.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="corpus mode: process only first N statutes from corpus CSV (default: all)",
    )
    sa_p.add_argument(
        "--label",
        metavar="LABEL",
        help="corpus mode: save CSV as data/bench_runs/LABEL_step_attr.csv",
    )
    sa_p.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help="corpus mode: number of parallel workers (default: cpu_count/2)",
    )
    sa_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="single-statute mode: print per-section divergence list",
    )

    # --- audit-trail ---
    audit_p = sub.add_parser(
        "audit-trail",
        help="per-amendment decision chain for one statute",
        description=(
            "Show the pipeline decisions made for each amendment: "
            "johtolause text, citation routing, PEG extraction result, "
            "and body content summary.  Reads from .cache/pipeline_gold.db."
        ),
    )
    audit_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    audit_p.add_argument(
        "--db",
        metavar="PATH",
        help="capture store DB path (default: .cache/pipeline_gold.db)",
    )

    # --- lower-audit ---
    lower_audit_p = sub.add_parser(
        "lower-audit",
        help="audit lowering pipeline preservation (ParsedOp -> LegalOp)",
        description=(
            "Verify that the lowering pipeline (ParsedOp -> ClauseAST -> "
            "LegalOperation) preserves semantic information: actions, targets, "
            "and facets. Runs on all amendments for a statute, or a single "
            "amendment with --source."
        ),
    )
    lower_audit_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    lower_audit_p.add_argument(
        "--source",
        metavar="AMEND",
        help="audit only this amendment (e.g. 2017/794)",
    )

    # --- sweden ---
    from lawvm.tools.sweden import register_cli as _register_sweden

    _register_sweden(sub)

    # --- finland rulebook ---
    fr_p = sub.add_parser(
        "finland-rulebook",
        help="render or validate the frozen Finland rulebook scaffold",
        description=(
            "Render the frozen Finland rulebook as deterministic Markdown, "
            "or validate that its governed vocabulary and structural invariants "
            "still hold."
        ),
    )
    fr_p.add_argument(
        "--validate",
        action="store_true",
        help="validate the rulebook instead of rendering it",
    )
    fr_p.add_argument(
        "--write-dir",
        metavar="DIR",
        help="write generated RULEBOOK.md and RULE_INDEX.json into DIR",
    )

    # --- scaffold ---
    scaffold_p = sub.add_parser(
        "scaffold",
        help="generate a blocked jurisdiction starter skeleton",
        description=(
            "Create src/lawvm/<jurisdiction>/ with contract-first blocked P5 "
            "starter helpers. The generated package preserves inventoried source "
            "units as non-claim evidence and does not claim replay support."
        ),
    )
    scaffold_p.add_argument(
        "jurisdiction",
        help="jurisdiction name, e.g. 'norway' or 'sweden' (lower-case, a-z/0-9/_)",
    )

    # --- check-consistency ---
    cc_p = sub.add_parser(
        "check-consistency",
        help="replay vs timeline internal consistency checker (Track F)",
        description=(
            "Verifies that the replay tree and compiled timelines are mutually coherent "
            "for a Finnish statute.  Reports SECTION_NO_TIMELINE, TIMELINE_NO_SECTION, "
            "CONTENT_DRIFT (internal structural checks), plus REPLAY_EXTRA / "
            "REPLAY_MISSING vs oracle.  Not a bench tool — checks internal invariants."
        ),
    )
    cc_p.add_argument(
        "statute_id",
        nargs="?",
        help="statute ID, e.g. 2002/738 (omit for --corpus mode)",
    )
    cc_p.add_argument(
        "--corpus",
        action="store_true",
        help="run over the standard bench corpus instead of a single statute",
    )
    cc_p.add_argument(
        "--corpus-path",
        dest="corpus_path",
        metavar="CSV",
        help="custom corpus CSV (default: data/finland/bench_corpus.csv)",
    )
    cc_p.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="corpus mode: process only first N statutes",
    )
    cc_p.add_argument(
        "--label",
        metavar="LABEL",
        help="corpus mode: save per-statute CSV to data/bench_runs/LABEL_consistency.csv",
    )
    cc_p.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="corpus mode: parallel workers (default: 1)",
    )
    cc_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show per-issue detail in single-statute mode; progress in corpus mode",
    )

    # --- verify-consistency ---
    vc_p = sub.add_parser(
        "verify-consistency",
        help="compare ops-replay vs consolidated text (Estonian: legal findings)",
        description=(
            "For Estonia: replays amendment chain from base statute and compares "
            "against a later consolidated (authoritative) version. Divergences "
            "are legal findings — the official text may differ from what the "
            "amendment chain produces. Accepts Riigi Teataja globaalIDs or XML paths."
        ),
    )
    vc_p.add_argument("--base", required=True, metavar="ID_OR_PATH", help="base statute (globaalID or .xml path)")
    vc_p.add_argument(
        "--consolidated", required=True, metavar="ID_OR_PATH", help="consolidated statute (globaalID or .xml path)"
    )
    vc_p.add_argument("--jurisdiction", default="ee", choices=["ee"], help="jurisdiction (default: ee)")
    vc_p.add_argument(
        "--as-of", dest="as_of", default="0000-00-00", help="date for comparison YYYY-MM-DD (default: 0000-00-00)"
    )
    vc_p.add_argument(
        "--cache-dir", dest="cache_dir", metavar="DIR", help="directory for cached XMLs (default: .tmp/estonia/)"
    )
    vc_p.add_argument("--verbose", "-v", action="store_true", help="show full text for all divergences")
    vc_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- ee-residual-inventory ---
    ee_residual_p = sub.add_parser(
        "ee-residual-inventory",
        help="print deterministic EE residual adjudication inventory",
        description=(
            "Show the known evidence-backed residual inventory for non-zero "
            "commensurable Estonia pairs. Without pair arguments, prints all "
            "known inventories; with --base-id and --oracle-id, filters to one pair."
        ),
        parents=_P,
    )
    ee_residual_p.add_argument(
        "--base-id",
        dest="base_id",
        metavar="ID",
        help="EE base statute globaalID",
    )
    ee_residual_p.add_argument(
        "--oracle-id",
        dest="oracle_id",
        metavar="ID",
        help="EE oracle/consolidated statute globaalID",
    )
    ee_residual_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- ee-residual-proposal ---
    ee_proposal_p = sub.add_parser(
        "ee-residual-proposal",
        help="propose residual inventory entries from a bench run",
        description=(
            "Takes a saved EE bench run, finds rows with open unexplained divergences, "
            "runs replay on each, and proposes candidate residual inventory entries "
            "with evidence text. Use --format python to emit code for residual_inventory.py."
        ),
        parents=_P,
    )
    ee_proposal_p.add_argument(
        "--label",
        metavar="LABEL",
        help="EE bench run label to scan for open rows",
    )
    ee_proposal_p.add_argument(
        "--base-id",
        dest="base_id",
        metavar="ID",
        help="EE base statute globaalID (alternative to --label)",
    )
    ee_proposal_p.add_argument(
        "--oracle-id",
        dest="oracle_id",
        metavar="ID",
        help="EE oracle statute globaalID (alternative to --label)",
    )
    ee_proposal_p.add_argument(
        "--title",
        metavar="TEXT",
        help="optional display title for single-pair mode",
    )
    ee_proposal_p.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="process top N open rows (default: 10)",
    )
    ee_proposal_p.add_argument(
        "--format",
        choices=["text", "json", "python"],
        default="text",
        help="output format: text (default), json, or python code for residual_inventory.py",
    )

    # --- ee-frontier ---
    ee_frontier_p = sub.add_parser(
        "ee-frontier",
        help="rank EE bench rows by open vs adjudicated residuals",
        description=(
            "Load a saved EE bench run and surface active frontier rows where "
            "open unexplained divergences remain, separately from fully adjudicated "
            "non-zero residual rows."
        ),
        parents=_P,
    )
    ee_frontier_p.add_argument(
        "--label",
        metavar="LABEL_OR_PATH",
        help="EE bench run label or direct CSV path; default is latest saved EE run",
    )
    ee_frontier_p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="show top N rows per bucket (default: 20)",
    )
    ee_frontier_p.add_argument(
        "--include-adjudicated",
        dest="include_adjudicated",
        action="store_true",
        help="also include adjudicated non-zero rows in the main rows payload",
    )
    ee_frontier_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- ee-chain-quality ---
    ee_chain_quality_p = sub.add_parser(
        "ee-chain-quality",
        help="run consecutive-pair replay quality over an EE version chain",
        description=(
            "For each consecutive pair in one Estonia terviktekst chain, replay "
            "the base to the next consolidated-version date and report divergence totals."
        ),
        parents=_P,
    )
    ee_chain_quality_p.add_argument(
        "grupi_ids",
        nargs="*",
        help="EE terviktekstiGrupiID values; default is a small built-in sample set",
    )

    # --- ee-pair-status ---
    ee_pair_status_p = sub.add_parser(
        "ee-pair-status",
        help="score one EE base/oracle pair with residual-bucket summary",
        description=(
            "Run the same pair-scoring path used by ee-bench for one explicit "
            "base/oracle pair and print matched/open adjudicated residual counts."
        ),
        parents=_P,
    )
    ee_pair_status_p.add_argument("--base-id", required=True, metavar="ID", help="EE base statute globaalID")
    ee_pair_status_p.add_argument("--oracle-id", required=True, metavar="ID", help="EE oracle statute globaalID")
    ee_pair_status_p.add_argument("--title", metavar="TEXT", help="optional display title")
    ee_pair_status_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- ee-explain ---
    ee_explain_p = sub.add_parser(
        "ee-explain",
        help="single-statute deep-dive for Estonia (divergences + residual buckets + source chain)",
        description=(
            "Shows all divergences with residual bucket classification, evidence text, "
            "comparison class, and source chain context for one EE base/oracle pair."
        ),
        parents=_P,
    )
    ee_explain_p.add_argument("--base-id", required=True, metavar="ID", help="EE base statute globaalID")
    ee_explain_p.add_argument("--oracle-id", required=True, metavar="ID", help="EE oracle statute globaalID")
    ee_explain_p.add_argument("--verbose", "-v", action="store_true", help="show full text and residual evidence")
    ee_explain_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- ee-publication-db ---
    ee_pub_p = sub.add_parser(
        "ee-publication-db",
        help="build Estonia divergence SQLite DB from current replayable corpus",
        description=(
            "Replay current/latest Estonia corpus pairs and store pair metadata "
            "plus replay-vs-Riigi-Teataja divergences in a browser-friendly "
            "SQLite DB. Use this with ee-corpus current, not the legacy 343-case slice."
        ),
        parents=_P,
    )
    ee_pub_p.add_argument(
        "--corpus",
        default="data/estonia/current_replayable_corpus.csv",
        metavar="CSV",
        help="current replayable Estonia corpus CSV (default: data/estonia/current_replayable_corpus.csv)",
    )
    ee_pub_p.add_argument(
        "--output",
        default="data/estonia/ee_divergences_publication.db",
        metavar="PATH",
        help="output SQLite path (default: data/estonia/ee_divergences_publication.db)",
    )
    ee_pub_p.add_argument(
        "--db",
        default="data/ee_riigiteataja.farchive",
        metavar="PATH",
        help="Riigi Teataja farchive path (default: data/ee_riigiteataja.farchive)",
    )
    ee_pub_p.add_argument("--limit", type=int, metavar="N", help="process only first N corpus rows")
    ee_pub_p.add_argument("--workers", type=int, default=1, metavar="N", help="parallel replay workers (default: 1)")

    # --- residual-ledger ---
    residual_ledger_p = sub.add_parser(
        "residual-ledger",
        help="validate or scaffold Finland residual-ledger CSV rows",
        description=(
            "Validate a residual-ledger CSV against the Tranche 0 row shape, "
            "or scaffold one CSV row from a saved phase-witness JSON artifact."
        ),
    )
    residual_ledger_sub = residual_ledger_p.add_subparsers(
        dest="residual_ledger_command",
        metavar="<action>",
        required=True,
    )
    residual_validate_p = residual_ledger_sub.add_parser(
        "validate",
        help="validate a residual-ledger CSV against the Tranche 0 schema",
    )
    residual_validate_p.add_argument(
        "path",
        nargs="?",
        default="notes/RESIDUAL_BUG_LEDGER_TEMPLATE.csv",
        help="CSV path to validate (default: notes/RESIDUAL_BUG_LEDGER_TEMPLATE.csv)",
    )
    residual_validate_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    residual_row_p = residual_ledger_sub.add_parser(
        "row",
        help="scaffold one residual-ledger CSV row from a phase-witness JSON artifact",
    )
    residual_row_p.add_argument(
        "--witness",
        required=True,
        metavar="PATH",
        help="path to phase-witness JSON",
    )
    residual_row_p.add_argument(
        "--observed-symptom",
        required=True,
        metavar="TEXT",
        help="observed symptom text for the ledger row",
    )
    residual_row_p.add_argument(
        "--path",
        metavar="LEDGER_PATH",
        help="override the path column; defaults to the witness target path",
    )
    residual_row_p.add_argument(
        "--interaction-family",
        metavar="FAMILY",
        default="",
        help="interaction family label",
    )
    residual_row_p.add_argument(
        "--suspected-first-bad-phase",
        metavar="PHASE",
        default="",
        help="optional suspected first bad phase",
    )
    residual_row_p.add_argument(
        "--confirmed-first-bad-phase",
        metavar="PHASE",
        default="",
        help="optional confirmed first bad phase",
    )
    residual_row_p.add_argument(
        "--secondary-phase",
        metavar="PHASE",
        default="",
        help="optional secondary phase",
    )
    residual_row_p.add_argument(
        "--source-pathology-present",
        metavar="YESNO",
        default="",
        help="optional yes/no/unknown source pathology flag",
    )
    residual_row_p.add_argument(
        "--oracle-or-editorial-witness-drift",
        metavar="YESNO",
        default="",
        help="optional yes/no/unknown oracle or editorial witness drift flag",
    )
    residual_row_p.add_argument(
        "--fix-owner",
        metavar="OWNER",
        default="",
        help="optional fix owner tag",
    )
    residual_row_p.add_argument(
        "--regression-ids",
        metavar="IDS",
        default="",
        help="optional regression ids / family tags",
    )
    residual_row_p.add_argument(
        "--status",
        metavar="STATUS",
        default="open",
        help="status value to write (default: open)",
    )
    residual_row_p.add_argument(
        "--notes",
        metavar="TEXT",
        default="",
        help="optional free-form notes",
    )
    residual_row_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of CSV text",
    )

    # --- destructive-repair-ledger ---
    destructive_repair_ledger_p = sub.add_parser(
        "destructive-repair-ledger",
        help="emit the seeded Tranche 0 destructive-repair family ledger",
        description=(
            "Print the current audited destructive-repair family ledger as markdown "
            "or JSON so Tranche 0 work can track ownership by mechanism rather than statute."
        ),
    )
    destructive_repair_ledger_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- ee-inspect-source ---
    ee_inspect_source_p = sub.add_parser(
        "ee-inspect-source",
        help="inspect one Estonia source act, target filtering, and compiled ops",
        description=(
            "Fetch one EE source act, resolve an optional target statute from --base-id "
            "or --target-title, summarize source sections, and preview compiled operations."
        ),
        parents=_P,
    )
    ee_inspect_source_p.add_argument("--source-id", required=True, metavar="ID", help="EE source act globaalID")
    ee_inspect_source_p.add_argument("--base-id", metavar="ID", help="optional EE base statute globaalID")
    ee_inspect_source_p.add_argument("--target-title", metavar="TEXT", help="optional explicit target statute title")
    ee_inspect_source_p.add_argument("--op-limit", type=int, default=25, metavar="N", help="max ops to print in preview (default: 25)")
    ee_inspect_source_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- ee-corpus ---
    ee_corpus_p = sub.add_parser(
        "ee-corpus",
        help="Estonia corpus acquisition and curation helpers",
        description=(
            "Acquire RT XMLs into the EE archive or curate reproducible EE corpus CSVs from the archive."
        ),
        parents=_P,
    )
    ee_corpus_sub = ee_corpus_p.add_subparsers(dest="ee_corpus_command", metavar="<subcommand>")

    ee_corpus_acquire_p = ee_corpus_sub.add_parser(
        "acquire",
        help="crawl RT publication feeds and fetch act XMLs into the archive",
    )
    ee_corpus_acquire_p.add_argument(
        "--db", default="data/ee_riigiteataja.farchive", metavar="PATH", help="Farchive DB path"
    )
    ee_corpus_acquire_p.add_argument(
        "--phase", type=int, choices=[1, 2], default=None, help="run only phase 1 (discover) or 2 (fetch)"
    )
    ee_corpus_acquire_p.add_argument(
        "--parts", default="2,3", metavar="CSV", help="comma-separated RT part ids (default: 2,3)"
    )
    ee_corpus_acquire_p.add_argument("--workers", type=int, default=4, metavar="N", help="parallel workers for phase 2")
    ee_corpus_acquire_p.add_argument(
        "--delay", type=float, default=0.8, metavar="SECONDS", help="delay between fetches in seconds"
    )

    ee_corpus_curate_p = ee_corpus_sub.add_parser(
        "curate",
        help="build the legacy small EE bench corpus CSV from the archive",
    )
    ee_corpus_curate_p.add_argument(
        "--db", default="data/ee_riigiteataja.farchive", metavar="PATH", help="Farchive DB path"
    )
    ee_corpus_curate_p.add_argument("--laws-only", action="store_true", help="include only law schemas, not decrees")
    ee_corpus_curate_p.add_argument("--output-csv", dest="output_csv", metavar="PATH", help="override output CSV path")
    ee_corpus_curate_p.add_argument(
        "--output-notes", dest="output_notes", metavar="PATH", help="override notes output path"
    )

    ee_corpus_current_p = ee_corpus_sub.add_parser(
        "current",
        help="build current/latest replayable Estonia comparison cases",
    )
    ee_corpus_current_p.add_argument(
        "--db", default="data/ee_riigiteataja.farchive", metavar="PATH", help="Farchive DB path"
    )
    ee_corpus_current_p.add_argument("--laws-only", action="store_true", help="include only law schemas, not decrees")
    ee_corpus_current_p.add_argument("--output-csv", dest="output_csv", metavar="PATH", help="override output CSV path")
    ee_corpus_current_p.add_argument(
        "--output-notes", dest="output_notes", metavar="PATH", help="override notes output path"
    )

    ee_corpus_replayable_p = ee_corpus_sub.add_parser(
        "replayable",
        help="build all consecutive replayable Estonia version-comparison cases",
    )
    ee_corpus_replayable_p.add_argument(
        "--db", default="data/ee_riigiteataja.farchive", metavar="PATH", help="Farchive DB path"
    )
    ee_corpus_replayable_p.add_argument("--laws-only", action="store_true", help="include only law schemas, not decrees")
    ee_corpus_replayable_p.add_argument("--output-csv", dest="output_csv", metavar="PATH", help="override output CSV path")
    ee_corpus_replayable_p.add_argument(
        "--output-notes", dest="output_notes", metavar="PATH", help="override notes output path"
    )

    ee_corpus_stats_p = ee_corpus_sub.add_parser(
        "stats",
        help="show EE archive statistics without re-indexing",
    )
    ee_corpus_stats_p.add_argument(
        "--db", default="data/ee_riigiteataja.farchive", metavar="PATH", help="Farchive DB path"
    )
    ee_corpus_stats_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- nz-corpus ---
    nz_corpus_p = sub.add_parser(
        "nz-corpus",
        help="New Zealand API v0 acquisition helpers",
        description=(
            "Acquire New Zealand Legislation API v0 work/version metadata and "
            "XML manifestations into farchive. Uses NZ_API_KEY from the "
            "environment and sends it only as an X-Api-Key header."
        ),
    )
    nz_corpus_sub = nz_corpus_p.add_subparsers(dest="nz_corpus_command", metavar="<subcommand>")
    nz_sync_p = nz_corpus_sub.add_parser(
        "sync",
        help="sync NZ API v0 metadata/XML into farchive",
        description=(
            "Resumable, rate-limit-aware acquisition. Existing locators are "
            "skipped unless --refetch is passed. Search discovery is used when "
            "no --work-id or --version-id is supplied."
        ),
    )
    nz_sync_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_sync_p.add_argument("--search-term", default="", metavar="TEXT", help="search term for /v0/works/")
    nz_sync_p.add_argument("--work-id", action="append", default=[], metavar="ID", help="work_id to sync")
    nz_sync_p.add_argument(
        "--version-id",
        action="append",
        default=[],
        metavar="ID",
        help="version_id to sync directly",
    )
    nz_sync_p.add_argument(
        "--legislation-type",
        default="",
        choices=["", "act", "amendment_paper", "bill", "secondary_legislation"],
        help="optional /v0/works legislation_type filter",
    )
    nz_sync_p.add_argument(
        "--publisher",
        default="",
        choices=["", "Agency", "Parliamentary Counsel Office"],
        help="optional /v0/works publisher filter",
    )
    nz_sync_p.add_argument(
        "--version-sort",
        default="desc",
        choices=["asc", "desc"],
        help="sort order for /v0/works/{work_id}/versions/ (default: desc)",
    )
    nz_sync_p.add_argument("--per-page", type=int, default=100, metavar="N", help="search page size, max 100")
    nz_sync_p.add_argument("--max-pages", type=int, default=None, metavar="N", help="maximum search pages")
    nz_sync_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_sync_p.add_argument("--max-versions", type=int, default=None, metavar="N", help="maximum versions")
    nz_sync_p.add_argument(
        "--max-versions-per-work",
        type=int,
        default=None,
        metavar="N",
        help="maximum versions to acquire for each work_id",
    )
    nz_sync_p.add_argument("--no-xml", action="store_true", help="capture API JSON only")
    nz_sync_p.add_argument("--refetch", action="store_true", help="refetch even when locator is already cached")
    nz_sync_p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="minimum delay between live requests (default: 0.5)",
    )
    nz_sync_p.add_argument(
        "--request-budget",
        type=int,
        default=None,
        metavar="N",
        help="stop after N live requests",
    )
    nz_sync_p.add_argument(
        "--reserve-remaining",
        type=int,
        default=100,
        metavar="N",
        help="stop when X-RateLimit-Remaining is <= N (default: 100)",
    )
    nz_sync_p.add_argument(
        "--sleep-on-rate-limit",
        action="store_true",
        help="sleep until the API reset time after 429/403 or quota-reserve stop, then continue",
    )
    nz_sync_p.add_argument(
        "--max-sleep-seconds",
        type=int,
        default=None,
        metavar="N",
        help="testing/supervisor guard: refuse a rate-limit sleep longer than N seconds",
    )
    nz_sync_p.add_argument(
        "--rate-limit-retry-attempts",
        type=int,
        default=3,
        metavar="N",
        help="short retries before sleeping until reset after HTTP 429/403 (default: 3)",
    )
    nz_sync_p.add_argument(
        "--diagnostics-jsonl",
        metavar="PATH",
        help="write acquisition diagnostics/failures as JSONL",
    )
    nz_sync_p.add_argument("--verbose", "-v", action="store_true", help="print progress details")
    nz_deps_p = nz_corpus_sub.add_parser(
        "deps",
        help="extract amendment dependency candidates from archived NZ XML",
        description=(
            "Read an archived NZ consolidated XML and extract amendment work "
            "candidates from reprint notes and provision-level history notes. "
            "This is evidence extraction, not replay."
        ),
    )
    nz_deps_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_deps_p.add_argument("--work-id", default="", metavar="ID", help="archived work_id whose latest XML to inspect")
    nz_deps_p.add_argument("--version-id", default="", metavar="ID", help="optional version_id label for explicit XML")
    nz_deps_p.add_argument("--xml-locator", default="", metavar="LOCATOR", help="explicit archived XML locator")
    nz_deps_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print in text mode")
    nz_deps_p.add_argument("--output-json", metavar="PATH", help="write full dependency report JSON")
    nz_deps_p.add_argument("--json", action="store_true", help="emit full dependency report JSON")
    nz_closure_p = nz_corpus_sub.add_parser(
        "closure",
        help="resumable NZ frontier acquisition",
        description=(
            "Acquire useful NZ source frontiers: target work versions/XML, "
            "dependency reports from latest XML, and latest XML for discovered "
            "amending works. With --sleep-on-rate-limit it can run under a "
            "supervisor and continue after quota resets."
        ),
    )
    nz_closure_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_closure_p.add_argument("--work-id", action="append", default=[], metavar="ID", help="seed work_id")
    nz_closure_p.add_argument(
        "--all-acts",
        action="store_true",
        help="sync latest versions/XML for all search-discovered Acts instead of dependency closure",
    )
    nz_closure_p.add_argument("--search-term", default="", metavar="TEXT", help="optional all-acts search term")
    nz_closure_p.add_argument(
        "--legislation-type",
        default="act",
        choices=["", "act", "amendment_paper", "bill", "secondary_legislation"],
        help="all-acts legislation_type filter (default: act)",
    )
    nz_closure_p.add_argument(
        "--publisher",
        default="",
        choices=["", "Agency", "Parliamentary Counsel Office"],
        help="optional all-acts publisher filter",
    )
    nz_closure_p.add_argument(
        "--dependency-depth",
        type=int,
        default=1,
        metavar="N",
        help="dependency expansion depth for seed work_ids (default: 1)",
    )
    nz_closure_p.add_argument(
        "--seed-latest-only",
        action="store_true",
        help="fetch only latest seed version instead of full seed version graph",
    )
    nz_closure_p.add_argument(
        "--max-versions-per-work",
        type=int,
        default=1,
        metavar="N",
        help="versions per non-seed/all-acts work (default: 1)",
    )
    nz_closure_p.add_argument("--version-sort", default="desc", choices=["asc", "desc"], help="version sort")
    nz_closure_p.add_argument("--per-page", type=int, default=100, metavar="N", help="API page size, max 100")
    nz_closure_p.add_argument("--max-pages", type=int, default=None, metavar="N", help="maximum search/version pages")
    nz_closure_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum discovered works")
    nz_closure_p.add_argument("--max-versions", type=int, default=None, metavar="N", help="maximum versions")
    nz_closure_p.add_argument("--no-xml", action="store_true", help="capture API JSON only")
    nz_closure_p.add_argument("--refetch", action="store_true", help="refetch even when locator is cached")
    nz_closure_p.add_argument("--delay", type=float, default=0.5, metavar="SECONDS", help="delay between requests")
    nz_closure_p.add_argument("--request-budget", type=int, default=None, metavar="N", help="stop after N requests")
    nz_closure_p.add_argument(
        "--reserve-remaining",
        type=int,
        default=100,
        metavar="N",
        help="stop when X-RateLimit-Remaining is <= N (default: 100)",
    )
    nz_closure_p.add_argument("--sleep-on-rate-limit", action="store_true", help="sleep until reset, then continue")
    nz_closure_p.add_argument("--max-sleep-seconds", type=int, default=None, metavar="N", help="sleep guard")
    nz_closure_p.add_argument(
        "--rate-limit-retry-attempts",
        type=int,
        default=3,
        metavar="N",
        help="short retries before reset sleep (default: 3)",
    )
    nz_closure_p.add_argument(
        "--diagnostics-jsonl",
        metavar="PATH",
        help="write latest sync phase diagnostics as JSONL",
    )
    nz_closure_p.add_argument(
        "--state-json",
        default=".tmp/nz_closure_state.json",
        metavar="PATH",
        help="write resumable closure state summary (default: .tmp/nz_closure_state.json)",
    )
    nz_closure_p.add_argument("--verbose", "-v", action="store_true", help="print rate-limit waits")
    nz_source_p = nz_corpus_sub.add_parser(
        "source-summary",
        help="parse archived NZ XML into a typed source-tree summary",
        description=(
            "Inspect archived NZ XML as source structure: labels, headings, "
            "provision paths, deletion status, and amendment-history witnesses. "
            "This does not lower to replay operations."
        ),
    )
    nz_source_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_source_p.add_argument("--work-id", default="", metavar="ID", help="work_id whose latest archived XML to parse")
    nz_source_p.add_argument("--xml-locator", default="", metavar="LOCATOR", help="explicit archived XML locator")
    nz_source_p.add_argument("--version-id", default="", metavar="ID", help="optional version_id label for explicit XML")
    nz_source_p.add_argument("--summary-only", action="store_true", help="omit source nodes from JSON output")
    nz_source_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print in text mode")
    nz_source_p.add_argument("--json", action="store_true", help="emit parsed source document JSON")
    nz_diff_p = nz_corpus_sub.add_parser(
        "version-diff",
        help="compare two archived NZ consolidated XML versions",
        description=(
            "Compare parsed source nodes between two archived consolidated XML "
            "versions. Defaults to latest vs previous archived version for the work."
        ),
    )
    nz_diff_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_diff_p.add_argument("--work-id", required=True, metavar="ID", help="work_id to compare")
    nz_diff_p.add_argument("--before-version-id", default="", metavar="ID", help="older version_id")
    nz_diff_p.add_argument("--after-version-id", default="", metavar="ID", help="newer version_id")
    nz_diff_p.add_argument(
        "--list-versions",
        action="store_true",
        help="list archived XML version witnesses for the work instead of diffing",
    )
    nz_diff_p.add_argument(
        "--version-date",
        default="",
        metavar="YYYY-MM-DD",
        help="with --list-versions, also report source-version date witnesses bracketing this date",
    )
    nz_diff_p.add_argument(
        "--change-window",
        action="store_true",
        help="with --list-versions --version-date, also report strict-before/on-or-after source witnesses",
    )
    nz_diff_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print in text mode")
    nz_diff_p.add_argument("--json", action="store_true", help="emit full diff JSON")
    nz_agreement_p = nz_corpus_sub.add_parser(
        "agreement",
        help="compare candidate NZ XML source tree against oracle XML",
        description=(
            "Compare two archived NZ XML source trees as candidate-vs-oracle "
            "agreement. This does not produce a candidate replay; it is the "
            "agreement metric surface future NZ replay should feed."
        ),
    )
    nz_agreement_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_agreement_p.add_argument("--candidate-xml-locator", required=True, metavar="LOCATOR")
    nz_agreement_p.add_argument("--oracle-xml-locator", required=True, metavar="LOCATOR")
    nz_agreement_p.add_argument("--candidate-version-id", default="", metavar="ID")
    nz_agreement_p.add_argument("--oracle-version-id", default="", metavar="ID")
    nz_agreement_p.add_argument("--limit", type=int, default=40, metavar="N", help="mismatch rows to print")
    nz_agreement_p.add_argument("--json", action="store_true", help="emit full agreement report JSON")
    nz_ops_p = nz_corpus_sub.add_parser(
        "operation-surface",
        help="extract typed NZ operation witnesses from history notes",
        description=(
            "Build a P5/P6 operation-witness surface from archived NZ XML "
            "history notes. This classifies source operation words and remains "
            "blocked for canonical effect lowering."
        ),
    )
    nz_ops_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_ops_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_ops_p.add_argument(
        "--limit",
        type=int,
        default=40,
        metavar="N",
        help="rows to print in text mode or include in JSON (default: 40)",
    )
    nz_ops_p.add_argument(
        "--summary-only",
        action="store_true",
        help="emit only operation-surface summary counts, omitting row payloads",
    )
    nz_ops_p.add_argument("--operation-family", default="", help="filter rows by classified operation family")
    nz_ops_p.add_argument("--target-address-status", default="", help="filter rows by target-address status")
    nz_ops_p.add_argument("--dependency-status", default="", help="filter rows by dependency status")
    nz_ops_p.add_argument("--lowering-readiness-status", default="", help="filter rows by lowering-readiness status")
    nz_ops_p.add_argument("--target-hint-status", default="", help="filter rows by target-hint status")
    nz_ops_p.add_argument(
        "--evidence-rows",
        action="store_true",
        help="include shared corpus evidence rows in JSON output",
    )
    nz_ops_p.add_argument(
        "--evidence-jsonl",
        metavar="PATH",
        help="write shared corpus operation/finding evidence rows as JSONL",
    )
    nz_ops_p.add_argument("--json", action="store_true", help="emit operation witness report JSON")
    nz_payload_p = nz_corpus_sub.add_parser(
        "payload-surface",
        help="link NZ operation witnesses to archived amending-act payload nodes",
        description=(
            "Build an archive-first payload witness surface from operation "
            "history-note amending-provision hrefs. This does not lower "
            "canonical effects or claim replay support."
        ),
    )
    nz_payload_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_payload_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_payload_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print/include")
    nz_payload_p.add_argument("--summary-only", action="store_true", help="emit only payload summary counts")
    nz_payload_p.add_argument("--payload-status", default="", help="filter rows by payload status")
    nz_payload_p.add_argument("--operation-family", default="", help="filter rows by operation family")
    nz_payload_p.add_argument("--instruction-shape", default="", help="filter rows by payload instruction shape")
    nz_payload_p.add_argument("--instruction-safety", default="", help="filter rows by payload instruction safety")
    nz_payload_p.add_argument("--json", action="store_true", help="emit payload witness report JSON")
    nz_effect_ready_p = nz_corpus_sub.add_parser(
        "effect-readiness",
        help="classify NZ rows that are ready for future canonical effect lowering",
        description=(
            "Combine operation and payload witness surfaces to classify "
            "pre-lowering readiness. This emits no canonical operations and "
            "does not claim replay support."
        ),
    )
    nz_effect_ready_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_effect_ready_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_effect_ready_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print/include")
    nz_effect_ready_p.add_argument("--summary-only", action="store_true", help="emit only readiness summary counts")
    nz_effect_ready_p.add_argument("--effect-readiness-status", default="", help="filter rows by readiness status")
    nz_effect_ready_p.add_argument("--operation-family", default="", help="filter rows by operation family")
    nz_effect_ready_p.add_argument("--payload-status", default="", help="filter rows by payload status")
    nz_effect_ready_p.add_argument(
        "--instruction-semantic-candidate-status",
        default="",
        help="filter rows by instruction semantic candidate status",
    )
    nz_effect_ready_p.add_argument(
        "--operation-target-address-status",
        default="",
        help="filter rows by original operation target-address status",
    )
    nz_effect_ready_p.add_argument("--json", action="store_true", help="emit readiness report JSON")
    nz_instruction_queue_p = nz_corpus_sub.add_parser(
        "instruction-workqueue",
        help="list NZ direct-instruction lowering candidates and blockers",
        description=(
            "Build a diagnostic work queue from NZ payload instruction-shape "
            "classification. This is not canonical lowering and emits no "
            "replay or agreement claim."
        ),
    )
    nz_instruction_queue_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_instruction_queue_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_instruction_queue_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print/include")
    nz_instruction_queue_p.add_argument("--summary-only", action="store_true", help="emit only workqueue summary counts")
    nz_instruction_queue_p.add_argument(
        "--queue-status",
        choices=("candidate", "review", "blocked", "not_required"),
        default="",
        help="filter rows by workqueue status",
    )
    nz_instruction_queue_p.add_argument("--instruction-family", default="", help="filter by instruction family")
    nz_instruction_queue_p.add_argument("--instruction-shape", default="", help="filter by payload instruction shape")
    nz_instruction_queue_p.add_argument("--instruction-subfamily-status", default="", help="filter by subfamily status")
    nz_instruction_queue_p.add_argument("--instruction-subfamily", default="", help="filter by instruction subfamily")
    nz_instruction_queue_p.add_argument(
        "--payload-structural-subfamily-status",
        default="",
        help="filter by report-only structural payload subfamily status",
    )
    nz_instruction_queue_p.add_argument(
        "--payload-structural-subfamily",
        default="",
        help="filter by report-only structural payload subfamily",
    )
    nz_instruction_queue_p.add_argument("--candidate-only", action="store_true", help="include only direct candidate rows")
    nz_instruction_queue_p.add_argument(
        "--evidence-rows",
        action="store_true",
        help="include shared evidence rows in JSON output",
    )
    nz_instruction_queue_p.add_argument(
        "--evidence-jsonl",
        metavar="PATH",
        help="write shared instruction-workqueue evidence rows as JSONL",
    )
    nz_instruction_queue_p.add_argument("--json", action="store_true", help="emit instruction workqueue report JSON")
    nz_effect_candidates_p = nz_corpus_sub.add_parser(
        "effect-candidates",
        help="emit NZ candidate canonical effects without replaying them",
        description=(
            "Build candidate LegalOperation envelopes for rows already proven "
            "ready for canonical effect lowering. Currently repeal and directly "
            "witnessed text-replacement candidates may be emitted; all other "
            "rows remain blocked with evidence."
        ),
    )
    nz_effect_candidates_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_effect_candidates_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_effect_candidates_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print/include")
    nz_effect_candidates_p.add_argument("--summary-only", action="store_true", help="emit only candidate summary counts")
    nz_effect_candidates_p.add_argument("--candidate-status", default="", help="filter rows by candidate status")
    nz_effect_candidates_p.add_argument("--action", default="", help="filter rows by emitted canonical action")
    nz_effect_candidates_p.add_argument("--operation-family", default="", help="filter rows by source operation family")
    nz_effect_candidates_p.add_argument("--blocking-rule", default="", help="filter rows by blocking rule id")
    nz_effect_candidates_p.add_argument(
        "--instruction-subfamily-status",
        default="",
        help="filter rows by instruction-workqueue subfamily status",
    )
    nz_effect_candidates_p.add_argument(
        "--instruction-subfamily",
        default="",
        help="filter rows by instruction-workqueue subfamily",
    )
    nz_effect_candidates_p.add_argument(
        "--payload-structural-subfamily-status",
        default="",
        help="filter rows by instruction-workqueue structural payload subfamily status",
    )
    nz_effect_candidates_p.add_argument(
        "--payload-structural-subfamily",
        default="",
        help="filter rows by instruction-workqueue structural payload subfamily",
    )
    nz_effect_candidates_p.add_argument(
        "--repeal-payload-corroboration-status",
        default="",
        help="filter rows by repeal payload corroboration status",
    )
    nz_effect_candidates_p.add_argument(
        "--operation-lowering-readiness-status",
        default="",
        help="filter rows by original operation lowering-readiness status",
    )
    nz_effect_candidates_p.add_argument(
        "--operation-target-address-status",
        default="",
        help="filter rows by original operation target-address status",
    )
    nz_effect_candidates_p.add_argument(
        "--operation-dependency-status",
        default="",
        help="filter rows by original operation dependency status",
    )
    nz_effect_candidates_p.add_argument(
        "--payload-instruction-shape",
        default="",
        help="filter rows by payload instruction shape",
    )
    nz_effect_candidates_p.add_argument(
        "--payload-instruction-safety",
        default="",
        help="filter rows by payload instruction safety classification",
    )
    nz_effect_candidates_p.add_argument(
        "--instruction-semantic-candidate-status",
        default="",
        help="filter rows by instruction semantic candidate status",
    )
    nz_effect_candidates_p.add_argument(
        "--latest-oracle-text-status",
        default="",
        help="filter rows by latest-oracle text witness status",
    )
    nz_effect_candidates_p.add_argument(
        "--text-replace-witness-support-status",
        default="",
        help="filter rows by text-replacement witness support classification",
    )
    nz_effect_candidates_p.add_argument(
        "--source-change-text-witness-status",
        default="",
        help="filter rows by archived source-change text witness status",
    )
    nz_effect_candidates_p.add_argument("--evidence-rows", action="store_true", help="include shared evidence rows in JSON output")
    nz_effect_candidates_p.add_argument("--evidence-jsonl", metavar="PATH", help="write shared candidate evidence rows as JSONL")
    nz_effect_candidates_p.add_argument("--json", action="store_true", help="emit candidate report JSON")
    nz_effect_preflight_p = nz_corpus_sub.add_parser(
        "candidate-preflight",
        help="dry-run NZ candidate replay preconditions without applying operations",
        description=(
            "Refuse dry-run replay unless every operation witness row has a "
            "candidate canonical effect. This checks preconditions only and "
            "does not mutate or materialize legal text."
        ),
    )
    nz_effect_preflight_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_effect_preflight_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_effect_preflight_p.add_argument("--limit", type=int, default=40, metavar="N", help="blocked rows to print/include")
    nz_effect_preflight_p.add_argument("--summary-only", action="store_true", help="emit only preflight summary counts")
    nz_effect_preflight_p.add_argument("--evidence-rows", action="store_true", help="include shared evidence rows in JSON output")
    nz_effect_preflight_p.add_argument("--evidence-jsonl", metavar="PATH", help="write shared preflight evidence rows as JSONL")
    nz_effect_preflight_p.add_argument("--json", action="store_true", help="emit preflight report JSON")
    nz_evidence_pack_p = nz_corpus_sub.add_parser(
        "evidence-pack",
        help="write one report-query-compatible NZ evidence JSONL pack",
        description=(
            "Bundle existing NZ operation witness, effect candidate, "
            "candidate preflight, and instruction-workqueue evidence rows. "
            "This creates no new replay or agreement claim."
        ),
    )
    nz_evidence_pack_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_evidence_pack_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_evidence_pack_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to include in JSON output")
    nz_evidence_pack_p.add_argument(
        "--surface",
        choices=("operation-surface", "effect-candidates", "candidate-preflight", "instruction-workqueue"),
        default="",
        help="filter evidence rows by NZ source surface",
    )
    nz_evidence_pack_p.add_argument(
        "--row-kind",
        choices=("operation", "finding"),
        default="",
        help="filter evidence rows by shared row kind",
    )
    nz_evidence_pack_p.add_argument("--status", default="", help="filter operation evidence rows by shared status")
    nz_evidence_pack_p.add_argument("--rule-id", default="", help="filter evidence rows by rule/finding id")
    nz_evidence_pack_p.add_argument("--blocking", action="store_true", help="filter to blocking evidence rows")
    nz_evidence_pack_p.add_argument("--output-jsonl", metavar="PATH", help="write shared evidence rows as JSONL")
    nz_evidence_pack_p.add_argument("--json", action="store_true", help="emit evidence-pack report JSON")
    nz_benchmark_p = nz_corpus_sub.add_parser(
        "benchmark",
        help="report archive-first NZ replay readiness coverage",
        description=(
            "Build a benchmark coverage report from archived NZ API/XML data. "
            "This measures source-tree, dependency, and snapshot-diff coverage "
            "and emits blocked replay status until canonical NZ effects exist."
        ),
    )
    nz_benchmark_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_benchmark_p.add_argument(
        "--work-id",
        action="append",
        default=[],
        metavar="ID",
        help="specific work_id to include; defaults to all archived version details",
    )
    nz_benchmark_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_benchmark_p.add_argument(
        "--include-diffs",
        action="store_true",
        help="compare latest archived XML to previous archived XML where available",
    )
    nz_benchmark_p.add_argument(
        "--include-payloads",
        action="store_true",
        help="resolve operation witnesses to archived amending-act payload nodes where possible",
    )
    nz_benchmark_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print in text mode")
    nz_benchmark_p.add_argument("--output-json", metavar="PATH", help="write full benchmark report JSON")
    nz_benchmark_p.add_argument("--json", action="store_true", help="emit full benchmark report JSON")

    # --- verify-chain ---
    verify_chain_p = sub.add_parser(
        "verify-chain",
        help="per-amendment PIT checkpoint verification (blame matrix)",
        description=(
            "For each amendment in the statute's chain, compare the LawVM replay "
            "state against the Finlex PIT XML snapshot (fin@YYYYNNNN). "
            "Produces a blame matrix showing where divergence first appears. "
            "Also compares final replay state against the live HTML website."
        ),
    )
    verify_chain_p.add_argument(
        "sids",
        nargs="+",
        help="statute ID(s) to verify, e.g. 2020/369",
    )
    verify_chain_p.add_argument(
        "--no-html",
        action="store_true",
        dest="no_html",
        help="skip HTML comparison (faster; no network request)",
    )
    verify_chain_p.add_argument(
        "--output",
        metavar="DIR",
        help="output directory for JSON results (default: .tmp/verify_chain/)",
    )

    # --- verify ---
    verify_p = sub.add_parser(
        "verify",
        help="run pipeline invariant checks at every stage",
        description=(
            "Run well-formedness checks after each pipeline stage. "
            "Default: full pipeline (parse + extract per amendment + apply per amendment). "
            "--stage parse: base statute checks only. "
            "--stage extract: ops from one amendment (requires --source). "
            "--stage observations: validate PhaseResult observation kinds and temporal_events."
        ),
    )
    verify_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    verify_p.add_argument(
        "--stage",
        choices=["parse", "extract", "apply", "observations"],
        help="limit to one pipeline stage (default: full pipeline)",
    )
    verify_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="amendment to check (required for --stage extract)",
    )
    verify_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for full pipeline (default: finlex_oracle)",
    )
    verify_p.add_argument(
        "--facade",
        dest="facade",
        action="store_true",
        help=(
            "--stage observations: also print CompileFacade summary "
            "(observations, temporal_events, quirks_used, source_completeness_issues, "
            "strictness) merged from all amendment PhaseResults"
        ),
    )
    verify_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable verification JSON",
    )

    # --- peg-audit ---
    peg_audit_p = sub.add_parser(
        "peg-audit",
        help="verify scan/filter pipeline preserves structural tokens",
        description=(
            "Phase 4 audit: for each amendment, tokenize the johtolause, "
            "run the scan/filter annotation pipeline, and verify that every "
            "structural token (PYKALA, LUKU, OSA, MOMENTTI, KOHTA, LIITE) "
            "either passes through to the structural view or is covered by "
            "a named annotation span.  UNACCOUNTED tokens indicate information "
            "loss in the pipeline."
        ),
    )
    peg_audit_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    peg_audit_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="audit only this amendment (default: all amendments)",
    )

    # --- peg-rules ---
    peg_rules_p = sub.add_parser(
        "peg-rules",
        help="list all registered Finland parse rules with examples",
        description=(
            "Phase 8 rule registry: list all Finland parse rules as first-class "
            "inspectable objects. Each rule has a stable ID, description, node kind, "
            "category, and example inputs. You can inspect a rule family without "
            "reading parser control flow."
        ),
    )
    peg_rules_p.add_argument(
        "--category",
        metavar="CAT",
        help="filter by category (structural, insertion, sub_ref, resolution, renumber, meta, text_amend)",
    )
    peg_rules_p.add_argument(
        "--node-kind",
        dest="node_kind",
        metavar="KIND",
        help="filter by node kind (e.g. SurfaceTargetRef, SurfaceInsertion, SurfaceMetaClause)",
    )
    peg_rules_p.add_argument(
        "--examples",
        action="store_true",
        help="show example inputs for each rule",
    )
    peg_rules_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- drift ---
    drift_p = sub.add_parser(
        "drift",
        help="measure content drift between base XML and oracle (source-quality check)",
        description=(
            "Content drift = sections where the base XML encoding differs from the "
            "Finlex oracle encoding despite no amendment touching them.  "
            "This is a source-quality issue, not a pipeline accuracy issue.  "
            "Requires a populated capture DB (.cache/pipeline_gold.db) for "
            "accurate touched-label tracking in corpus mode."
        ),
    )
    drift_p.add_argument(
        "--statute",
        metavar="SID",
        help="single statute to analyse, e.g. 2009/953",
    )
    drift_p.add_argument(
        "--corpus",
        action="store_true",
        help="run across all statutes in the capture DB",
    )
    drift_p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="show worst N statutes in corpus mode (default: 20)",
    )
    drift_p.add_argument(
        "--output",
        metavar="CSV",
        help="write per-statute drift summary to CSV",
    )
    drift_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show per-section detail in single-statute mode; progress in corpus mode",
    )

    # --- bench-regression-guard ---
    brg_p = sub.add_parser(
        "bench-regression-guard",
        help="compare saved bench runs and fail on excessive regressions",
        description=(
            "Compare two saved bench run CSVs, report per-statute regressions "
            "and improvements, and exit non-zero if configured limits are exceeded."
        ),
    )
    brg_p.add_argument("--baseline", required=True, help="baseline bench run label")
    brg_p.add_argument("--current", required=True, help="current bench run label")
    brg_p.add_argument(
        "--threshold", type=float, default=0.005, help="per-statute regression threshold (default: 0.005)"
    )
    brg_p.add_argument(
        "--max-regressions",
        type=int,
        default=3,
        dest="max_regressions",
        help="max allowed statutes regressing beyond threshold (default: 3)",
    )

    # --- sync-finlex ---
    sync_p = sub.add_parser(
        "sync-finlex",
        help="incremental sync of Finlex Open Data API changes",
        description=(
            "Fetch consolidated statutes changed since a datetime from the "
            "Finlex Open Data API v1 and store them in a Farchive database. "
            "Uses publishedSince parameter for incremental updates."
        ),
    )
    sync_p.add_argument(
        "--since",
        required=True,
        metavar="DATETIME",
        help="ISO 8601 datetime for publishedSince, e.g. 2026-03-01T00:00:00Z",
    )
    sync_p.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: data/finlex.farchive)",
    )
    sync_p.add_argument(
        "--doc-type",
        dest="doc_type",
        default="statute-consolidated",
        choices=["statute", "statute-consolidated"],
        help="document type to sync (default: statute-consolidated)",
    )
    sync_p.add_argument(
        "--lang",
        default="fin",
        help="language filter: 'fin', 'swe', or '' for both (default: fin)",
    )
    sync_p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="delay between requests in seconds (default: 1.0)",
    )
    sync_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="list changes without fetching or storing",
    )
    sync_p.add_argument(
        "--list-only",
        dest="list_only",
        action="store_true",
        help="alias for --dry-run",
    )
    sync_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print one line per statute to stderr",
    )

    # --- sync-finlex-latest ---
    sync_latest_p = sub.add_parser(
        "sync-finlex-latest",
        help="sync Finnish PIT XMLs for known statutes into farchive",
        description=(
            "Enumerate the Finnish statute IDs already known to the archive "
            "(or an optional corpus CSV) and fetch every discovered PIT XML "
            "version for each statute. Existing exact PIT XML locators are "
            "skipped."
        ),
    )
    sync_latest_p.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: data/finlex.farchive)",
    )
    sync_latest_p.add_argument(
        "--sid",
        action="append",
        default=[],
        metavar="STATUTE_ID",
        help="optional statute ID to sync (repeatable; overrides corpus/archive defaults)",
    )
    sync_latest_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="optional corpus CSV of statute IDs (default: archive source IDs)",
    )
    sync_latest_p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="delay between statutes in seconds (default: 1.0)",
    )
    sync_latest_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print one line per statute to stderr",
    )
    sync_latest_p.add_argument(
        "--diagnostics-jsonl",
        metavar="PATH",
        help="write acquisition diagnostics for skipped/error PIT sync rows",
    )

    # --- solver-diag ---
    solver_diag_p = sub.add_parser(
        "solver-diag",
        help="CP-SAT solver diagnostic for subsection slot assignment",
        description=(
            "Run the CP-SAT constraint solver alongside the heuristic slot "
            "assignment chain for a single statute.  Reports per-amendment "
            "solver status (unique/ambiguous/infeasible) and any disagreements "
            "with the heuristic.  Phase 1 pilot: diagnostic only."
        ),
        parents=_P,
    )
    solver_diag_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    solver_diag_p.add_argument(
        "--source",
        metavar="AMEND",
        help="restrict to one amendment, e.g. 2017/794",
    )
    solver_diag_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-slot details for each amendment",
    )

    # --- import-zip ---
    import_zip_p = sub.add_parser(
        "import-zip",
        help="bulk import Finlex ZIP files into farchive",
        description=(
            "One-time import of statute source XMLs and/or consolidated oracle XMLs "
            "(including GIF media) from Finlex Open Data ZIP distribution into a "
            "content-addressed farchive DB. Handles large ZIPs (680K+ entries) in "
            "streaming batches. Accepts either local ZIP paths or Finlex archive URLs. "
            "Use --skip-existing to resume interrupted imports."
        ),
    )
    import_zip_p.add_argument(
        "--statute-zip",
        dest="statute_zip",
        metavar="PATH",
        help="path or URL to source corpus ZIP (source XMLs)",
    )
    import_zip_p.add_argument(
        "--consolidated-zip",
        dest="consolidated_zip",
        metavar="PATH",
        help="path or URL to consolidated corpus ZIP (oracle XMLs + media)",
    )
    import_zip_p.add_argument(
        "--dest",
        metavar="PATH",
        default="data/finlex.farchive",
        help="farchive DB path (default: data/finlex.farchive)",
    )
    import_zip_p.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        help="skip entries already present in farchive (resume mode)",
    )
    import_zip_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would be imported without writing to farchive",
    )
    import_zip_p.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=2000,
        metavar="N",
        help="number of entries per store_batch commit (default: 2000)",
    )

    # --- structural-review ---
    sr_p = sub.add_parser(
        "structural-review",
        help="interactive structural diff review and classification",
        description=(
            "Iterate through sections with structural differences, "
            "classify each as ok/noise/source-pathology/bug. "
            "Classifications persist across runs in .tmp/structural_review_classifications.jsonl."
        ),
        parents=_P,
    )
    sr_p.add_argument("statute_id", nargs="?", help="statute ID, e.g. 2004/301")
    sr_p.add_argument("--section", help="filter to one section, e.g. '17 §'")
    sr_p.add_argument("--stats", action="store_true", help="show classification stats")
    sr_p.add_argument("--unreviewed", action="store_true", help="show statutes with unreviewed diffs")
    sr_p.add_argument("--all", action="store_true", help="include already-classified sections")
    sr_p.add_argument("--corpus-summary", action="store_true", help="corpus-wide diff severity ranking")
    sr_p.add_argument("--corpus-scan", metavar="FILE", help="parallel live scan from statute list file (e.g. .tmp/statutes.txt)")
    sr_p.add_argument("--workers", type=int, default=0, help="parallel workers for corpus-scan (default: cpu_count)")
    sr_p.add_argument(
        "--dump",
        action="store_true",
        help="non-interactive LLM-consumable dump of structural diffs (combine with --section for one section)",
    )
    sr_p.add_argument("--compact", action="store_true", help="with --dump: omit identical nodes, show only diffs")
    sr_p.add_argument("--triple", action="store_true", help="with --dump: three-column LawVM / Finlex XML / Finlex HTML view")
    sr_p.add_argument("--cache-only", action="store_true", dest="cache_only", help="with --triple: skip live HTML fetch, use cached HTML only")
    sr_p.add_argument("--replay-only", action="store_true", dest="replay_only", help="dump full LawVM replay text (all sections, no diff)")
    sr_p.add_argument("--oracle-only", action="store_true", dest="oracle_only", help="dump full Finlex oracle text (all sections, no diff)")
    sr_p.add_argument(
        "--oracle-selector-mode",
        default="bench_comparable",
        choices=["latest_cached_editorial", "bench_comparable"],
        help="consolidated oracle selector for structural review (default: bench_comparable)",
    )

    # --- structural-grep / sgrep ---
    sg_p = sub.add_parser(
        "structural-grep",
        aliases=["sgrep"],
        help="corpus-wide semantic structure query",
        description=(
            "Iterate over corpus statutes, build semantic structures, and apply "
            "user-specified filters on the semantic structure nodes.  All filters "
            "combine with AND logic."
        ),
        parents=_P,
    )
    # Structural predicate filters
    sg_p.add_argument("--replay-label-basis", action="append", metavar="V", help="replay label_basis equals V (repeatable)")
    sg_p.add_argument("--oracle-label-basis", action="append", metavar="V", help="oracle label_basis equals V (repeatable)")
    sg_p.add_argument("--diff-kind", action="append", metavar="V", help="diff kind equals V (repeatable)")
    sg_p.add_argument("--diff-event", action="append", metavar="V", help="diff event kind equals V (repeatable)")
    sg_children = sg_p.add_mutually_exclusive_group()
    sg_children.add_argument("--has-children", action="store_true", help="section has children")
    sg_children.add_argument("--no-children", action="store_true", help="section has no children")
    sg_p.add_argument("--replay-missing", action="store_true", help="replay side absent")
    sg_p.add_argument("--oracle-missing", action="store_true", help="oracle side absent")
    # Text regex filters
    sg_p.add_argument("--oracle-text-matches", metavar="RE", help="oracle text matches regex")
    sg_p.add_argument("--replay-text-matches", metavar="RE", help="replay text matches regex")
    sg_p.add_argument("--oracle-text-not-matches", metavar="RE", help="oracle text does NOT match regex")
    sg_p.add_argument("--replay-text-not-matches", metavar="RE", help="replay text does NOT match regex")
    # Op-level filters
    sg_p.add_argument("--has-op", action="append", metavar="TYPE", help="section has op of type (REPEAL, REPLACE, INSERT)")
    sg_p.add_argument("--no-op", action="append", metavar="TYPE", help="section does NOT have op of type")
    # Negation filters
    sg_p.add_argument("--not-diff-kind", action="append", metavar="V", help="diff kind is NOT V (repeatable)")
    sg_p.add_argument("--not-oracle-label-basis", action="append", metavar="V", help="oracle label_basis is NOT V (repeatable)")
    sg_p.add_argument("--not-replay-label-basis", action="append", metavar="V", help="replay label_basis is NOT V (repeatable)")
    # Corpus / parallelism
    sg_p.add_argument("--corpus", metavar="FILE", help="corpus file path (CSV or text; default: bench_core.csv)")
    sg_p.add_argument("--parallel", type=int, default=0, metavar="N", help="worker count (0=cpu_count, 1=sequential)")
    # Output modes
    sg_p.add_argument("--verbose", "-v", action="store_true", help="include text snippets in output")
    sg_p.add_argument("--count", action="store_true", help="count matches per statute")
    sg_p.add_argument("--json", dest="json_output", action="store_true", help="full JSON output")

    # --- export-projections ---
    ep_p = sub.add_parser(
        "export-projections",
        help="export canonical LawVM projections to JSONL/Parquet",
        description=(
            "Project canonical LawVM objects (statutes, sections, findings, ops) "
            "into JSONL files for SQL analytics via 'lawvm sql'. "
            "Optionally writes Parquet if pyarrow is available."
        ),
        parents=_P,
    )
    ep_p.add_argument("--corpus", metavar="PATH", help="path to corpus CSV (default: bench_core.csv)")
    ep_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=".tmp/projections",
        help="output directory for projections (default: .tmp/projections)",
    )
    ep_p.add_argument("--workers", type=int, default=0, help="parallel workers (default: cpu_count, max 8)")
    ep_p.add_argument(
        "--mode",
        default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode (default: finlex_oracle)",
    )
    ep_p.add_argument("--limit", type=int, metavar="N", help="process only first N statutes")

    # --- report ---
    report_p = sub.add_parser(
        "report",
        help="query shared LawVM evidence-row JSONL reports",
        description=(
            "Read JSONL report files that either are shared evidence rows or contain an "
            "evidence_row object. This command filters only the shared evidence envelope; "
            "frontend-specific rendering remains with frontend tools."
        ),
    )
    report_sub = report_p.add_subparsers(dest="report_command", metavar="<report-command>")
    report_query_p = report_sub.add_parser("query", help="filter shared evidence-row JSONL reports")
    report_query_p.add_argument("paths", nargs="+", help="JSONL report path(s)")
    report_query_p.add_argument("--row-id", default="", metavar="ID", help="operation row_id or finding_id")
    report_query_p.add_argument("--status", default="", metavar="STATUS", help="shared row status")
    report_query_p.add_argument("--rule-id", default="", metavar="RULE", help="finding rule_id or operation finding_id")
    report_query_p.add_argument("--phase", default="", metavar="PHASE", help="finding/report phase")
    report_query_p.add_argument("--source-artifact", default="", metavar="ID", help="source artifact id")
    report_query_p.add_argument("--source-unit", default="", metavar="ID", help="source unit id")
    report_query_p.add_argument("--locator", default="", metavar="LOC", help="source locator or evidence codify_path")
    report_query_p.add_argument("--blocking", action="store_true", help="keep only blocking rows")
    report_query_p.add_argument(
        "--detail",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="filter by evidence detail/evidence field; may be repeated",
    )
    report_query_p.add_argument("--limit", type=int, default=20, metavar="N", help="maximum rows to emit")
    report_query_p.add_argument("--validate", action="store_true", help="validate selected rows against the shared envelope")
    report_query_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- open-law ---
    open_law_p = sub.add_parser(
        "open-law",
        help="inspect and audit Open Law Library XML operations",
        description=(
            "Parse Open Law Library XML and codify:* action files. "
            "This frontend audits structured Open Law operations; it does not "
            "infer amendments from prose."
        ),
    )
    open_law_sub = open_law_p.add_subparsers(dest="open_law_command", metavar="<open-law-command>")
    open_law_ops_p = open_law_sub.add_parser("ops", help="list codify operations in an Open Law action XML file")
    open_law_ops_p.add_argument("action_xml", help="path to editorial-actions/*.xml")
    open_law_ops_p.add_argument("--json", action="store_true", help="emit JSON")
    open_law_replay_p = open_law_sub.add_parser("replay", help="replay Open Law codify operations over one XML tree")
    open_law_replay_p.add_argument("base_xml", help="path to base Open Law XML")
    open_law_replay_p.add_argument("action_xml", help="path to editorial-actions/*.xml")
    open_law_replay_p.add_argument(
        "--path-prefix",
        default="",
        metavar="A|B",
        help="explicit carried parent path for partial subtree files, e.g. 10|41",
    )
    open_law_replay_p.add_argument("--strict", action="store_true", help="mark unsupported actions as blocking")
    open_law_replay_p.add_argument("--text", action="store_true", help="include materialized text in output")
    open_law_replay_p.add_argument("--json", action="store_true", help="emit JSON")
    open_law_audit_p = open_law_sub.add_parser(
        "audit",
        help="compare replay of Open Law actions against an after XML snapshot",
    )
    open_law_audit_p.add_argument("before_xml", help="path to before Open Law XML")
    open_law_audit_p.add_argument("after_xml", help="path to after Open Law XML")
    open_law_audit_p.add_argument("action_xml", help="path to editorial-actions/*.xml")
    open_law_audit_p.add_argument(
        "--path-prefix",
        default="",
        metavar="A|B",
        help="explicit carried parent path for partial subtree files, e.g. 10|41",
    )
    open_law_audit_p.add_argument("--strict", action="store_true", help="mark unsupported actions as blocking")
    open_law_audit_p.add_argument("--json", action="store_true", help="emit JSON")
    open_law_inv_p = open_law_sub.add_parser("inventory", help="write Maryland Open Law local-repo inventory manifest")
    open_law_inv_p.add_argument("--source-repo", required=True, metavar="PATH", help="local maryland-dsd/law-xml clone")
    open_law_inv_p.add_argument("--codified-repo", required=True, metavar="PATH", help="local law-xml-codified clone")
    open_law_inv_p.add_argument("--out", default=".tmp/open_law/report", metavar="DIR", help="output directory")
    open_law_corpus_p = open_law_sub.add_parser("corpus-audit", help="audit Maryland publication transitions")
    open_law_corpus_p.add_argument("--source-repo", required=True, metavar="PATH", help="local maryland-dsd/law-xml clone")
    open_law_corpus_p.add_argument("--codified-repo", required=True, metavar="PATH", help="local law-xml-codified clone")
    open_law_corpus_p.add_argument("--before-branch", default="", metavar="BRANCH", help="before publication branch")
    open_law_corpus_p.add_argument("--after-branch", default="", metavar="BRANCH", help="after publication branch")
    open_law_corpus_p.add_argument("--out", default=".tmp/open_law/report", metavar="DIR", help="output directory")
    open_law_corpus_p.add_argument("--limit", type=int, metavar="N", help="audit only first N operations")
    open_law_corpus_p.add_argument("--strict", action="store_true", help="mark unsupported actions as blocking")
    open_law_corpus_p.add_argument("--json", action="store_true", help="emit summary JSON")
    open_law_pack_p = open_law_sub.add_parser("evidence-pack", help="write a Maryland Open Law demo evidence pack")
    open_law_pack_p.add_argument("--source-repo", required=True, metavar="PATH", help="local maryland-dsd/law-xml clone")
    open_law_pack_p.add_argument("--codified-repo", required=True, metavar="PATH", help="local law-xml-codified clone")
    open_law_pack_p.add_argument("--out", default=".tmp/open_law/evidence-pack", metavar="DIR", help="output directory")
    open_law_pack_p.add_argument("--limit", type=int, metavar="N", help="audit only first N operations")
    open_law_pack_p.add_argument("--strict", action="store_true", help="mark unsupported actions as blocking")
    open_law_pack_p.add_argument("--json", action="store_true", help="emit summary JSON")
    open_law_verify_pack_p = open_law_sub.add_parser(
        "verify-pack",
        help="verify Open Law evidence-pack checksums and evidence envelopes",
    )
    open_law_verify_pack_p.add_argument(
        "--report-dir",
        default=".tmp/open_law/evidence-pack",
        metavar="DIR",
        help="directory with evidence_pack_manifest.json and JSONL evidence rows",
    )
    open_law_verify_pack_p.add_argument(
        "--require-clean-generator",
        action="store_true",
        help="require evidence_pack_manifest.json to name a clean LawVM git commit",
    )
    open_law_verify_pack_p.add_argument("--json", action="store_true", help="emit verification JSON")
    open_law_explain_p = open_law_sub.add_parser("explain", help="explain rows from an Open Law corpus report")
    open_law_explain_p.add_argument("--report-dir", default=".tmp/open_law/evidence-pack", metavar="DIR", help="directory with operation_audits.jsonl")
    open_law_explain_p.add_argument("--op-id", default="", metavar="ID", help="specific operation row id")
    open_law_explain_p.add_argument("--status", default="", metavar="STATUS", help="filter rows by status")
    open_law_explain_p.add_argument("--limit", type=int, default=5, metavar="N", help="maximum rows to print")
    open_law_explain_p.add_argument("--json", action="store_true", help="emit matching rows as JSON")

    # --- sql ---
    sql_p = sub.add_parser(
        "sql",
        help="ad-hoc SQL over LawVM canonical projections (DuckDB)",
        description=(
            "Run SQL queries against JSONL/Parquet projections produced by "
            "'lawvm export-projections'. Uses DuckDB as the local analytics backend. "
            "Without --query, shows available tables and schema."
        ),
        parents=_P,
    )
    sql_p.add_argument("--query", "-q", metavar="SQL", help="SQL query to execute")
    sql_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=".tmp/projections",
        help="directory containing projection files (default: .tmp/projections)",
    )
    sql_p.add_argument(
        "--format",
        dest="output_format",
        default="table",
        choices=["table", "json", "csv"],
        help="output format (default: table)",
    )

    # --- bench-report ---
    bench_report_p = sub.add_parser(
        "bench-report",
        help="summarise a bench run CSV without re-running the bench",
        description=(
            "Read the latest (or a named) bench run CSV from data/bench_runs/ "
            "and show a ranked summary of statute scores."
        ),
    )
    bench_report_p.add_argument(
        "--run",
        metavar="FILE",
        default="",
        help="bench CSV file (default: latest in data/bench_runs/)",
    )
    bench_report_p.add_argument(
        "--bottom",
        type=int,
        default=20,
        metavar="N",
        help="show N worst-scoring statutes (default: 20)",
    )
    bench_report_p.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="show N best-scoring statutes",
    )
    bench_report_p.add_argument(
        "--threshold",
        type=float,
        default=0.999,
        metavar="SIM",
        help="similarity threshold (default: 0.999)",
    )
    bench_report_p.add_argument(
        "--errors-only",
        action="store_true",
        help="only show rows with status != OK",
    )
    bench_report_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- parse-johto ---
    parse_johto_p = sub.add_parser(
        "parse-johto",
        help="parse a Finnish amendment johtolause text and show parsed ops",
        description=(
            "Parse a Finnish amendment johtolause clause string and print the "
            "parsed ops. Useful for debugging the johtolause parser."
        ),
    )
    parse_johto_p.add_argument("text", help="johtolause text to parse")
    parse_johto_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        default="",
        help="statute context (optional)",
    )
    parse_johto_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="also show raw AST tokens",
    )
    parse_johto_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    return parser


def _has_uk_replay_regime_flags(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "uk_allow_metadata_backfill", None) is not None
        or getattr(args, "uk_allow_oracle_alignment", None) is not None
        or getattr(args, "uk_respect_feed_applied", None) is not None
        or getattr(args, "uk_applicability_mode", None) is not None
        or bool(getattr(args, "uk_source_first_candidate", False))
        or getattr(args, "uk_authority_mode", None) is not None
        or getattr(args, "uk_allow_metadata_only_effects", None) is not None
    )


def _reject_uk_replay_regime_flags_for_non_uk(args: argparse.Namespace, *, command: str) -> None:
    jurisdiction = str(getattr(args, "jurisdiction", "fi") or "fi")
    if jurisdiction == "uk" or not _has_uk_replay_regime_flags(args):
        return
    print(
        f"ERROR: UK replay regime flags on '{command}' are only supported with -j uk",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "bisect":
        from lawvm.tools.bisect import main as bisect_main

        bisect_main(args)

    elif args.command == "bisect-section":
        from lawvm.tools.bisect_section import main as bisect_section_main

        bisect_section_main(args)

    elif args.command == "dump":
        from lawvm.tools.dump import main as dump_main

        dump_main(args)

    elif args.command == "source-dump":
        from lawvm.tools.source_dump import main as source_dump_main

        source_dump_main(args)

    elif args.command == "inspect-amendment":
        from lawvm.tools.inspect_amendment import main as inspect_amendment_main

        inspect_amendment_main(args)

    elif args.command == "diagnose-phase":
        from lawvm.tools.diagnose_phase import main as diagnose_phase_main

        diagnose_phase_main(args)

    elif args.command == "invariant-bisect":
        from lawvm.tools.invariant_bisect import main as invariant_bisect_main

        invariant_bisect_main(args)

    elif args.command == "snapshot-debug":
        from lawvm.tools.snapshot_debug import main as snapshot_debug_main

        snapshot_debug_main(args)

    elif args.command == "product-debug":
        from lawvm.tools.product_debug import main as product_debug_main

        product_debug_main(args)

    elif args.command == "phase-witness":
        from lawvm.tools.phase_witness import main as phase_witness_main

        phase_witness_main(args)

    elif args.command == "oracle-context":
        from lawvm.tools.oracle_context import main as oracle_context_main

        oracle_context_main(args)

    elif args.command == "oracle-text":
        from lawvm.tools.oracle_text import main as oracle_text_main

        oracle_text_main(args)

    elif args.command == "replay-plan":
        from lawvm.tools.replay_plan import main as replay_plan_main

        replay_plan_main(args)

    elif args.command == "trace-section":
        from lawvm.tools.trace_section import main as trace_section_main

        trace_section_main(args)

    elif args.command == "explain":
        j = getattr(args, "jurisdiction", "fi")
        if j == "ee":
            if not getattr(args, "oracle_id", ""):
                print("ERROR: lawvm explain -j ee requires --oracle-id", file=sys.stderr)
                raise SystemExit(2)
            from lawvm.tools.ee_explain import main as ee_explain_main

            args.base_id = args.statute_id
            args.oracle_id = args.oracle_id
            ee_explain_main(args)
        elif j == "fi":
            if getattr(args, "json", False):
                print("ERROR: lawvm explain --json is currently only supported for -j ee", file=sys.stderr)
                raise SystemExit(2)
            from lawvm.tools.explain import main as explain_main

            explain_main(args)
        else:
            print(f"ERROR: lawvm explain does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

    elif args.command == "classify":
        from lawvm.tools.classify import main as classify_main

        classify_main(args)

    elif args.command == "bench":
        j = getattr(args, "jurisdiction", "fi")
        _reject_uk_replay_regime_flags_for_non_uk(args, command="bench")
        if j == "ee":
            from lawvm.tools.ee_bench import main as ee_bench_main

            ee_bench_main(args)
        elif j == "uk":
            from lawvm.tools.uk_bench import main as uk_bench_main

            uk_bench_main(args)
        else:
            from lawvm.tools.bench import main as bench_main

            bench_main(args)

    elif args.command == "blame":
        j = getattr(args, "jurisdiction", "fi")
        if j == "ee":
            from lawvm.tools.ee_blame import main as ee_blame_main

            ee_blame_main(args)
        else:
            from lawvm.tools.blame import main as blame_main

            blame_main(args)

    elif args.command == "replay":
        j = getattr(args, "jurisdiction", "fi")
        _reject_uk_replay_regime_flags_for_non_uk(args, command="replay")
        if j == "ee":
            from lawvm.tools.ee_replay import main as ee_replay_main

            ee_replay_main(args)
        elif j == "no":
            from lawvm.tools.no_replay import main as no_replay_main

            no_replay_main(args)
        elif j == "uk":
            # Map replay args to uk-replay convention:
            # replay uses base_id + --as-of; uk-replay uses statute_id + --pit-date
            args.statute_id = args.base_id
            args.pit_date = getattr(args, "as_of", None)
            args.enacted_only = False
            args.db = getattr(args, "archive", None)
            from lawvm.tools.uk_replay import main as uk_replay_main

            uk_replay_main(args)
        elif j == "fi":
            from lawvm.finland.grafter import replay_xml

            as_of = getattr(args, "as_of", "")
            verbose = getattr(args, "verbose", False)
            show_text = getattr(args, "show_text", False)
            use_json = getattr(args, "json", False)
            replay_meta: dict[str, object] = {}
            result = replay_xml(
                args.base_id,
                mode="legal_pit",
                as_of=as_of,
                replay_meta_out=replay_meta,
                quiet=not verbose,
            )
            if use_json:
                import json as _json

                from lawvm.core.ir_helpers import irnode_to_text

                meta = {
                    "statute_id": args.base_id,
                    "as_of": as_of,
                    "mode": "legal_pit",
                    "title": result.title if result else "",
                    "sections": [],
                }
                if result and result.ir:
                    for child in result.ir.children:
                        meta["sections"].append({
                            "label": child.label or "",
                            "text": irnode_to_text(child)[:200] if show_text else "",
                        })
                print(_json.dumps(meta, indent=2, ensure_ascii=False))
            else:
                if result and result.ir:
                    from lawvm.core.ir_helpers import irnode_to_text

                    print(f"Replay {args.base_id} as-of {as_of}")
                    print(f"Title: {result.title}")
                    print(f"Sections: {len(result.ir.children)}")
                    if show_text:
                        for child in result.ir.children:
                            text = irnode_to_text(child)
                            print(f"\n  {child.label or '?'} §: {text[:300]}...")
                else:
                    print(f"No result for {args.base_id}")
        else:
            print(f"error: 'replay' not yet implemented for '{j}'", file=sys.stderr)
            sys.exit(1)

    elif args.command == "ee-residual-inventory":
        from lawvm.tools.ee_residual_inventory import main as ee_residual_inventory_main

        ee_residual_inventory_main(args)

    elif args.command == "ee-residual-proposal":
        from lawvm.tools.ee_residual_proposal import main as ee_residual_proposal_main

        ee_residual_proposal_main(args)

    elif args.command == "ee-frontier":
        from lawvm.tools.ee_frontier import main as ee_frontier_main

        ee_frontier_main(args)

    elif args.command == "ee-chain-quality":
        from lawvm.tools.ee_chain_quality import main as ee_chain_quality_main

        ee_chain_quality_main(args)

    elif args.command == "ee-pair-status":
        from lawvm.tools.ee_pair_status import main as ee_pair_status_main

        ee_pair_status_main(args)

    elif args.command == "ee-explain":
        from lawvm.tools.ee_explain import main as ee_explain_main

        ee_explain_main(args)

    elif args.command == "ee-publication-db":
        from lawvm.tools.ee_publication_db import main as ee_publication_db_main

        ee_publication_db_main(args)

    elif args.command == "residual-ledger":
        from lawvm.tools.residual_ledger import main as residual_ledger_main

        residual_ledger_main(args)

    elif args.command == "destructive-repair-ledger":
        from lawvm.tools.destructive_repair_ledger import main as destructive_repair_ledger_main

        destructive_repair_ledger_main(args)

    elif args.command == "ee-inspect-source":
        from lawvm.tools.ee_inspect_source import main as ee_inspect_source_main

        ee_inspect_source_main(args)

    elif args.command == "ee-corpus":
        from lawvm.tools.ee_corpus import main as ee_corpus_main

        ee_corpus_main(args)

    elif args.command == "nz-corpus":
        if args.nz_corpus_command == "sync":
            from lawvm.new_zealand.acquisition import main as nz_corpus_sync_main

            nz_corpus_sync_main(args)
        elif args.nz_corpus_command == "deps":
            from lawvm.new_zealand.dependencies import main as nz_corpus_deps_main

            nz_corpus_deps_main(args)
        elif args.nz_corpus_command == "closure":
            from lawvm.new_zealand.closure import main as nz_corpus_closure_main

            nz_corpus_closure_main(args)
        elif args.nz_corpus_command == "source-summary":
            from lawvm.new_zealand.source_tree import main as nz_corpus_source_summary_main

            nz_corpus_source_summary_main(args)
        elif args.nz_corpus_command == "version-diff":
            from lawvm.new_zealand.version_diff import main as nz_corpus_version_diff_main

            nz_corpus_version_diff_main(args)
        elif args.nz_corpus_command == "agreement":
            from lawvm.new_zealand.agreement import main as nz_corpus_agreement_main

            nz_corpus_agreement_main(args)
        elif args.nz_corpus_command == "operation-surface":
            from lawvm.new_zealand.operation_surface import main as nz_corpus_operation_surface_main

            nz_corpus_operation_surface_main(args)
        elif args.nz_corpus_command == "payload-surface":
            from lawvm.new_zealand.payload_surface import main as nz_corpus_payload_surface_main

            nz_corpus_payload_surface_main(args)
        elif args.nz_corpus_command == "effect-readiness":
            from lawvm.new_zealand.effect_readiness import main as nz_corpus_effect_readiness_main

            nz_corpus_effect_readiness_main(args)
        elif args.nz_corpus_command == "instruction-workqueue":
            from lawvm.new_zealand.instruction_workqueue import main as nz_corpus_instruction_workqueue_main

            nz_corpus_instruction_workqueue_main(args)
        elif args.nz_corpus_command == "effect-candidates":
            from lawvm.new_zealand.effect_candidates import main as nz_corpus_effect_candidates_main

            nz_corpus_effect_candidates_main(args)
        elif args.nz_corpus_command == "candidate-preflight":
            from lawvm.new_zealand.effect_candidates import preflight_main as nz_corpus_candidate_preflight_main

            nz_corpus_candidate_preflight_main(args)
        elif args.nz_corpus_command == "evidence-pack":
            from lawvm.new_zealand.evidence_pack import main as nz_corpus_evidence_pack_main

            nz_corpus_evidence_pack_main(args)
        elif args.nz_corpus_command == "benchmark":
            from lawvm.new_zealand.benchmark import main as nz_corpus_benchmark_main

            nz_corpus_benchmark_main(args)
        else:
            parser.error("nz-corpus requires a subcommand")

    elif args.command == "bench-regression-guard":
        from lawvm.tools.bench_regression_guard import main as bench_regression_guard_main

        bench_regression_guard_main(args)

    elif args.command == "no-inventory":
        from lawvm.tools.no_inventory import main as no_inventory_main

        no_inventory_main(args)

    elif args.command == "no-index":
        from lawvm.tools.no_index import main as no_index_main

        no_index_main(args)

    elif args.command == "no-ingest":
        from lawvm.tools.no_ingest import main as no_ingest_main

        no_ingest_main(args)

    elif args.command == "no-statsrad":
        from lawvm.tools.no_statsrad import main as no_statsrad_main

        no_statsrad_main(args)

    elif args.command == "no-commencement-report":
        from lawvm.tools.no_commencement_report import main as no_commencement_main

        no_commencement_main(args)

    elif args.command == "no-commencement-candidates":
        from lawvm.tools.no_commencement_candidates import main as no_commencement_candidates_main

        no_commencement_candidates_main(args)

    elif args.command == "no-commencement-backfill":
        from lawvm.tools.no_commencement_backfill import main as no_commencement_backfill_main

        no_commencement_backfill_main(args)

    elif args.command == "no-commencement-evidence-plan":
        from lawvm.tools.no_commencement_evidence_plan import main as no_commencement_evidence_plan_main

        no_commencement_evidence_plan_main(args)

    elif args.command == "no-blockers":
        from lawvm.tools.no_blockers import main as no_blockers_main

        no_blockers_main(args)

    elif args.command == "no-source":
        from lawvm.tools.no_source import main as no_source_main

        no_source_main(args)

    elif args.command == "no-source-excerpt":
        from lawvm.tools.no_source_excerpt import main as no_source_excerpt_main

        no_source_excerpt_main(args)

    elif args.command == "no-law":
        from lawvm.tools.no_law import main as no_law_main

        no_law_main(args)

    elif args.command == "no-op-trace":
        from lawvm.tools.no_op_trace import main as no_op_trace_main

        no_op_trace_main(args)

    elif args.command == "no-missing-base":
        from lawvm.tools.no_missing_base import main as no_missing_base_main

        no_missing_base_main(args)

    elif args.command == "no-commencement-validate":
        from lawvm.tools.no_commencement_validate import main as no_commencement_validate_main

        no_commencement_validate_main(args)

    elif args.command == "no-commencement-phrases":
        from lawvm.tools.no_commencement_phrases import main as no_commencement_phrases_main

        no_commencement_phrases_main(args)

    elif args.command == "no-impact":
        from lawvm.tools.no_impact import main as no_impact_main

        no_impact_main(args)

    elif args.command == "no-frontier":
        from lawvm.tools.no_frontier import main as no_frontier_main

        no_frontier_main(args)

    elif args.command == "no-divergence":
        from lawvm.tools.no_divergence import main as no_divergence_main

        no_divergence_main(args)

    elif args.command == "no-coverage":
        from lawvm.tools.no_coverage import main as no_coverage_main

        no_coverage_main(args)

    elif args.command == "no-debug":
        from lawvm.tools.no_debug import main as no_debug_main

        no_debug_main(args)

    elif args.command == "no-workqueue":
        from lawvm.tools.no_workqueue import main as no_workqueue_main

        no_workqueue_main(args)

    elif args.command == "no-progress":
        from lawvm.tools.no_progress import main as no_progress_main

        no_progress_main(args)

    elif args.command == "no-verify":
        from lawvm.tools.no_verify import main as no_verify_main

        no_verify_main(args)

    elif args.command == "no-verify-scan":
        from lawvm.tools.no_verify_scan import main as no_verify_scan_main

        no_verify_scan_main(args)

    elif args.command == "no-verify-partition":
        from lawvm.tools.no_verify_partition import main as no_verify_partition_main

        no_verify_partition_main(args)

    elif args.command == "no-verify-workqueue":
        from lawvm.tools.no_verify_workqueue import main as no_verify_workqueue_main

        no_verify_workqueue_main(args)

    elif args.command == "diff":
        from lawvm.tools.diff import main as diff_main

        diff_main(args)

    elif args.command == "ops":
        from lawvm.tools.ops import main as ops_main

        ops_main(args)

    elif args.command == "replay-debug":
        from lawvm.tools.replay_debug import main as replay_debug_main

        replay_debug_main(args)

    elif args.command == "replay-inspect":
        from lawvm.tools.replay_inspect import main as replay_inspect_main

        replay_inspect_main(args)

    elif args.command == "oracle-check":
        from lawvm.tools.oracle_check import main as oracle_check_main

        oracle_check_main(args)

    elif args.command == "gold":
        from lawvm.tools.gold import main as gold_main

        gold_main(args)

    elif args.command == "delegate":
        from lawvm.tools.delegate import main as delegate_main

        delegate_main(args)

    elif args.command == "cite":
        from lawvm.tools.cite import main as cite_main

        cite_main(args)

    elif args.command == "timeline":
        from lawvm.tools.timeline import main as timeline_main

        timeline_main(args)

    elif args.command == "export":
        from lawvm.tools.export import main as export_main

        export_main(args)

    elif args.command == "graph":
        from lawvm.tools.graph_query import main as graph_main

        graph_main(args)

    elif args.command == "build":
        from lawvm.tools.build import main as build_main

        build_main(args)

    elif args.command == "query":
        from lawvm.tools.query import main as query_main

        query_main(args)

    elif args.command == "census":
        from lawvm.tools.census import main as census_main

        census_main(args)

    elif args.command == "coverage":
        from lawvm.tools.coverage import main as coverage_main

        coverage_main(args)

    elif args.command == "corrigendum":
        from lawvm.tools.corrigendum import main as corrigendum_main

        corrigendum_main(args)

    elif args.command == "faults":
        from lawvm.tools.faults import main as faults_main

        faults_main(args)

    elif args.command in {"evidence", "prove-oracle", "evidence-review"}:
        from lawvm.tools.evidence import main as evidence_main

        evidence_main(args)

    elif args.command == "oracle-classify":
        from lawvm.tools.oracle_classify import main as oc_main

        oc_main(args)

    elif args.command == "bench-curate":
        from lawvm.tools.bench_curate import main as bench_curate_main

        bench_curate_main(args)

    elif args.command == "bench-hydrate":
        from lawvm.tools.bench_hydrate import main as bench_hydrate_main

        bench_hydrate_main(args)

    elif args.command == "audit":
        from lawvm.tools.audit import main as audit_main

        audit_main(args)

    elif args.command == "bilingual":
        from lawvm.tools.bilingual import main as bilingual_main

        bilingual_main(args)

    elif args.command == "failures":
        from lawvm.tools.failures import main as failures_main

        failures_main(
            statute_id=args.statute_id,
            pattern=args.pattern,
            top=args.top,
            verbose=args.verbose,
            detail=args.detail,
            from_bench=getattr(args, "from_bench", None),
            parallel=getattr(args, "parallel", 1),
            save_cache=getattr(args, "save_cache", None),
        )

    elif args.command == "uk-replay":
        from lawvm.tools.uk_replay import main as uk_replay_main

        uk_replay_main(args)

    elif args.command == "uk-fetch-affecting":
        from pathlib import Path
        from farchive import Farchive
        from lawvm.uk_legislation.uk_prefetch import fetch_missing_for_statute

        _repo_root_fa = Path(__file__).resolve().parents[3]
        _default_db_fa = _repo_root_fa / "data" / "uk_legislation.farchive"
        db_path = Path(args.db) if getattr(args, "db", None) else _default_db_fa
        if not db_path.exists():
            print(f"error: archive DB not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        archive = Farchive(db_path)
        try:
            report = fetch_missing_for_statute(
                args.statute_id,
                archive,
                dry_run=getattr(args, "dry_run", False),
                verbose=getattr(args, "verbose", False),
                include_enacted=getattr(args, "include_enacted_affecting", False),
            )
            fetched, cached, errors = report
        finally:
            archive.close()
        if getattr(args, "json", False):
            import json as _json

            if hasattr(report, "to_dict"):
                payload = report.to_dict()
            else:
                payload = {
                    "fetched_count": fetched,
                    "already_cached_count": cached,
                    "error_count": errors,
                    "events": [],
                }
            print(_json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"fetched={fetched}  already_cached={cached}  errors={errors}")
            if hasattr(report, "to_dict"):
                payload = report.to_dict()
                rule_counts = payload.get("event_rule_counts") or {}
                blocking_rule_counts = payload.get("blocking_event_rule_counts") or {}
                if rule_counts:
                    rule_text = ", ".join(
                        f"{rule}={count}" for rule, count in sorted(rule_counts.items())
                    )
                    print(f"event_rules={rule_text}")
                if blocking_rule_counts:
                    blocking_rule_text = ", ".join(
                        f"{rule}={count}" for rule, count in sorted(blocking_rule_counts.items())
                    )
                    print(f"blocking_event_rules={blocking_rule_text}")
        if errors:
            sys.exit(1)

    elif args.command == "uk-effect":
        from lawvm.tools.uk_effect import main as uk_effect_main

        uk_effect_main(args)

    elif args.command == "uk-effects":
        from lawvm.tools.uk_effects import main as uk_effects_main

        uk_effects_main(args)

    elif args.command == "uk-eids":
        from lawvm.tools.uk_eids import main as uk_eids_main

        uk_eids_main(args)

    elif args.command == "uk-candidates":
        from lawvm.tools.uk_candidates import main as uk_candidates_main

        uk_candidates_main(args)

    elif args.command == "eu-reul":
        from lawvm.tools.eu_reul import main as eu_reul_main

        eu_reul_main(args)

    elif args.command == "eu-replay":
        from lawvm.tools.eu_replay import main as eu_replay_main

        eu_replay_main(args)

    elif args.command == "disagreement":
        from lawvm.tools.disagreement import main as disagreement_main

        disagreement_main(args)

    elif args.command == "frontier":
        from lawvm.tools.frontier import main as frontier_main

        frontier_main(args)

    elif args.command == "strict-report":
        from lawvm.tools.strict_report import main as strict_report_main

        strict_report_main(args)

    elif args.command == "capture":
        from lawvm.tools.capture import main as capture_main

        capture_main(args)

    elif args.command == "audit-trail":
        from lawvm.tools.audit_trail import main as audit_trail_main

        audit_trail_main(args)

    elif args.command == "lower-audit":
        from lawvm.tools.lower_audit import main as lower_audit_main

        lower_audit_main(args)

    elif args.command == "scaffold":
        from lawvm.tools.scaffold import main as scaffold_main

        scaffold_main(args)

    elif args.command == "verify-chain":
        from lawvm.tools.verify_chain import main as verify_chain_main

        verify_chain_main(args)

    elif args.command == "check-consistency":
        from lawvm.tools.consistency import main as cc_main

        cc_main(args)

    elif args.command == "verify-consistency":
        from lawvm.tools.verify_consistency import main as vc_main

        vc_main(args)

    elif args.command == "verify":
        from lawvm.tools.verify import main as verify_main

        verify_main(args)

    elif args.command == "peg-audit":
        from lawvm.tools.peg_audit import main as peg_audit_main

        peg_audit_main(args)

    elif args.command == "peg-rules":
        from lawvm.tools.peg_rules import main as peg_rules_main

        peg_rules_main(args)

    elif args.command == "freshness":
        from lawvm.tools.freshness import main as freshness_main

        freshness_main(args)

    elif args.command == "step-attribution":
        from lawvm.tools.step_attribution import main as sa_main

        sa_main(args)

    elif args.command == "sweden":
        from lawvm.tools.sweden import main as sweden_main

        sweden_main(args)

    elif args.command == "finland-rulebook":
        from lawvm.tools.finland_rulebook import main as finland_rulebook_main

        finland_rulebook_main(args)

    elif args.command == "drift":
        from lawvm.tools.drift import main as drift_main

        drift_main(args)

    elif args.command == "sync-finlex":
        from pathlib import Path as _Path
        from farchive import Farchive as _FA
        from lawvm.finland.finlex_api import sync_changes as _sync_changes

        _default_db_sf = _Path("data/finlex.farchive")
        _db_path = _Path(args.db) if getattr(args, "db", None) else _default_db_sf
        _db_path.parent.mkdir(parents=True, exist_ok=True)

        _dry = getattr(args, "dry_run", False) or getattr(args, "list_only", False)
        _archive = _FA(_db_path)
        try:
            _stats = _sync_changes(
                archive=_archive,
                since=args.since,
                delay=args.delay,
                lang=args.lang,
                doc_type=args.doc_type,
                dry_run=_dry,
                verbose=getattr(args, "verbose", False),
            )
        finally:
            _archive.close()

        print(
            f"fetched={_stats['fetched']}  modified={_stats['modified']}  "
            f"added={_stats['added']}  deleted={_stats['deleted']}  "
            f"skipped={_stats['skipped']}  errors={_stats['errors']}"
        )
        if _stats["errors"]:
            sys.exit(1)

    elif args.command == "sync-finlex-latest":
        from lawvm.tools.sync_finlex_latest import main as sync_finlex_latest_main

        sync_finlex_latest_main(args)

    elif args.command == "solver-diag":
        from lawvm.tools.solver_slot_assignment import cli_solver_diag

        cli_solver_diag(args)

    elif args.command == "import-zip":
        from lawvm.tools.import_zip import main as import_zip_main

        import_zip_main(args)

    elif args.command == "structural-review":
        from lawvm.tools.structural_review import (
            review_sections,
            show_corpus_summary,
            show_stats,
            show_unreviewed,
        )

        if getattr(args, "replay_only", False) or getattr(args, "oracle_only", False):
            from lawvm.tools.structural_review import dump_single_side
            if not args.statute_id:
                print("ERROR: statute_id required for --replay-only / --oracle-only", file=sys.stderr)
                sys.exit(1)
            side = "replay" if args.replay_only else "oracle"
            sys.stdout.write(dump_single_side(
                args.statute_id, side=side,
                section_filter=getattr(args, "section", None),
                oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
            ))
        elif getattr(args, "dump", False):
            if getattr(args, "triple", False):
                from lawvm.tools.structural_review import dump_triple_view
                if not args.statute_id:
                    print("ERROR: statute_id required for --dump --triple", file=sys.stderr)
                    sys.exit(1)
                dump_triple_view(
                    args.statute_id,
                    cache_only=getattr(args, "cache_only", False),
                    section_filter=getattr(args, "section", None),
                    oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
                )
            elif getattr(args, "corpus_scan", None):
                from lawvm.tools.structural_review import dump_corpus
                dump_corpus(
                    args.corpus_scan,
                    workers=getattr(args, "workers", 0),
                    oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
                )
            elif args.statute_id:
                from lawvm.tools.structural_review import dump_statute
                result = dump_statute(
                    args.statute_id,
                    compact=getattr(args, "compact", False),
                    section_filter=getattr(args, "section", None),
                    oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
                )
                sys.stdout.write(result)
            else:
                print("--dump requires a statute ID or --corpus-scan FILE")
        elif args.stats:
            show_stats()
        elif args.unreviewed:
            show_unreviewed()
        elif getattr(args, "corpus_summary", False):
            show_corpus_summary()
        elif getattr(args, "corpus_scan", None):
            from lawvm.tools.structural_review import corpus_scan
            corpus_scan(
                args.corpus_scan,
                workers=getattr(args, "workers", 0),
                oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
            )
        else:
            review_sections(
                statute_filter=args.statute_id,
                section_filter=args.section,
                unreviewed_only=not args.all,
                oracle_selector_mode=getattr(args, "oracle_selector_mode", "bench_comparable"),
            )

    elif args.command in ("structural-grep", "sgrep"):
        from lawvm.tools.structural_grep import main as sgrep_main

        sgrep_main(args)

    elif args.command == "export-projections":
        from lawvm.tools.export_parquet import main as export_proj_main

        export_proj_main(args)

    elif args.command == "open-law":
        from lawvm.tools.open_law import main as open_law_main

        open_law_main(args)

    elif args.command == "report":
        from lawvm.tools.report_query import main as report_query_main

        report_query_main(args)

    elif args.command == "sql":
        from lawvm.tools.sql_query import main as sql_main

        sql_main(args)

    elif args.command == "bench-report":
        from lawvm.tools.bench_report import main as bench_report_main

        bench_report_main(args)

    elif args.command == "parse-johto":
        from lawvm.tools.parse_johto import main as parse_johto_main

        parse_johto_main(args)

    elif args.command is None:
        parser.print_help()
        sys.exit(1)

    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
