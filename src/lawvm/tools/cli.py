"""lawvm — unified developer CLI for LawVM.

Subcommands:
    bisect    <statute_id>  Find which amendment damages a statute's score.
    bisect-section <statute_id>  Find which amendment damages one section.
    dump      <statute_id>  Inspect pipeline state at a named stage.
    source-dump <statute_id>  Inspect raw archived source XML with line numbers.
    fi-source-label-audit     Compare Finland source XML label normalization policies.
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
    refs                            Query ReferenceMention cross-statute citations from fi_refs.parquet.
    preparatory-refs                Query PreparatoryReference preparation chain citations.
    inline-citations                Query InlineCitation body-prose citations from fi_inline_citations.parquet.
    pools                           Query PoolMention budget-line/quantity mentions from fi_pools.parquet.
    fi-proposals                    Query Finnish government proposals from fi_he_corpus.parquet.
    fi-proposal-show <HE_ID>        Per-HE structural overview (atoms, law_refs, signatures).
    fi-proposal-bundle --he HE_ID   Typed JSON bundle aggregating #1-#5 projections for one HE.
    fi-proposal-history --statute S All HEs that touched a statute (legislative history).
    fi-proposals-competing --statute S Pending HEs that concurrently amend the same statute.
    sync-fi-proposals               Acquire HE corpus and rebuild fi_he_* Parquet projections.
    rebuild-indexes                 Regenerate Tier 2 Parquet projections from Tier 1 farchive.
    build-index-db                  Compose Tier 2 Parquets into a single DuckDB .db file.
    bench-report                    Summarise a bench run CSV without re-running the bench.
    parse-johto <text>              Parse a Finnish amendment johtolause text and show parsed ops.
    fi-parse-explain <sid>          Dump everything needed to diagnose one statute's johtolause parse.
    fi-parse                        Visualize Finnish parse structures (forest/johtolause/morph/clauses).
    fi-refs <sid>                   Annotated-source-canvas viewer for the references overlay.
    topic --topic STRING            Keyword/FTS search across statute sections and HE body atoms.
    follow-refs --start REF         Multi-hop reference traversal from a provision.
    pit-timeline --provision REF    Provision amendment history (index-backed).
    pit-diff --provision REF        Provision diff between two PIT dates (index-backed).
    provision-state <statute_id>    Stable PIT provision-state JSON seam output.
    provenance <statute_id>         Trace in-force wording to amendment, HE, and preparatory refs.
    trace <statute_id>              Alias for provenance.
    telos [--statute STATUTE_ID]    Query telos/purpose sections (feature #5).
    claim propose|accept|reject|retract|list|show  Manual compilation claims (Slices 1+2).

Usage:
    lawvm bisect 2006/1299
    lawvm bisect 2006/1299 --verbose
    lawvm bisect-section 2006/1299 --section '63 §'
    lawvm dump 2006/1299 --after parse
    lawvm dump ukpga/2002/30 --db data/uk_legislation.farchive
    lawvm dump 2006/1299 --after extract --source 2017/794
    lawvm source-dump 2006/1299 --address 'chapter:3/section:12'
    lawvm fi-source-label-audit 2006/1299 --json
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
import importlib
import os
import re
import sys
from collections.abc import Iterator
from typing import TextIO

from lawvm.tools.uk_replay_regime import UK_APPLICABILITY_MODE_CHOICES
from lawvm.tools.uk_replay_regime import add_uk_replay_regime_arguments
from lawvm.tools.replay_mode_arg import replay_mode_argument

# Inlined from lawvm.core.invariant_detectors.SUPPORTED_INVARIANT_DETECTORS.
# Used only as argparse choices= — no need to import the full module (which pulls
# replay_lints, tree_ops, icontract) just to serve --help.
# KEEP IN SYNC with invariant_detectors.py::SUPPORTED_INVARIANT_DETECTORS.
# Drift is caught by tests/test_invariant_detectors.py::test_cli_inlined_choices_match.
_INVARIANT_DETECTOR_CHOICES: tuple[str, ...] = (
    "duplicate_label",
    "label_normalization_collision",
    "illegal_edge",
    "sort_order",
    "mixed_hierarchy",
    "all_tree",
    "text_duplication",
    "flattened_sublist_family",
    "label_sequence_gap",
    "descendant_sibling_loss",
    "same_source_descendant_snapshot_shadow",
)


def _oracle_version_amendment_id(value: str) -> str:
    if re.fullmatch(r"\d{4}/\d{1,4}", value) is None:
        raise argparse.ArgumentTypeError("expected oracle version amendment id in YYYY/NNN form")
    return value


_FI_NUMERIC_ID_RE = re.compile(r"^\s*(\d{1,4})/(\d{1,6})\s*$")
_FI_CLI_ID_FIELD_TOKENS = (
    "id",
    "statute",
    "amendment",
    "source",
    "before",
    "after",
    "base",
    "oracle_version",
)


def _iter_cli_string_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _iter_cli_string_values(item)


def _looks_like_cli_id_field(field_name: str) -> bool:
    return any(token in field_name for token in _FI_CLI_ID_FIELD_TOKENS)


def _cli_field_label(field_name: str) -> str:
    if field_name == "statute_id":
        return "statute_id"
    return f"--{field_name.replace('_', '-')}"


def _reject_pre_1734_fi_command_line_ids(
    args: argparse.Namespace,
    *,
    stream: TextIO | None = None,
) -> None:
    """Reject CLI-supplied Finnish IDs whose year component predates 1734."""

    if getattr(args, "jurisdiction", "fi") != "fi":
        return
    stream = sys.stderr if stream is None else stream
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []
    for field_name, value in vars(args).items():
        if not _looks_like_cli_id_field(field_name):
            continue
        for text in _iter_cli_string_values(value):
            match = _FI_NUMERIC_ID_RE.fullmatch(text)
            if match is None:
                continue
            first, second = match.groups()
            year = int(first)
            if year >= 1734:
                continue
            key = (field_name, text)
            if key in seen:
                continue
            seen.add(key)
            label = _cli_field_label(field_name)
            if int(second) >= 1734:
                errors.append(
                    f"ERROR: invalid Finnish ID '{text}' in {label}: year {year} is before 1734. "
                    f"Finnish IDs must use year/num; use '{second}/{first}' for year {second} "
                    f"number {first}."
                )
            else:
                errors.append(
                    f"ERROR: invalid Finnish ID '{text}' in {label}: year {year} is before 1734. "
                    "Finnish IDs must use year/num."
                )
    if errors:
        for error in errors:
            print(error, file=stream)
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    jurisdiction_default = os.environ.get("LAWVM_JURISDICTION", "fi")

    # Root parent provides the default; subcommand parent suppresses its default
    # so `lawvm -j uk evidence-review ...` is not overwritten by the subparser.
    _j_root_parent = argparse.ArgumentParser(add_help=False)
    _j_root_parent.add_argument(
        "-j",
        "--jurisdiction",
        default=jurisdiction_default,
        choices=["fi", "ee", "uk", "no", "nz", "us"],
        help="jurisdiction (default: fi, or LAWVM_JURISDICTION env var)",
    )
    _j_subcommand_parent = argparse.ArgumentParser(add_help=False)
    _j_subcommand_parent.add_argument(
        "-j",
        "--jurisdiction",
        default=argparse.SUPPRESS,
        choices=["fi", "ee", "uk", "no", "nz", "us"],
        help="jurisdiction (default: fi, or LAWVM_JURISDICTION env var)",
    )

    _CAPABILITY_MAP = """\
lawvm — point-in-time legal state + citation graph + amendment history across jurisdictions (fi/ee/uk/no/nz). Select with -j.

  FIND   topic (FTS text) · sgrep (structural) · refs / cite (citation graph, fwd+reverse) · fi-proposals (FI HE corpus)
  READ   oracle-text (section @version) · provision-state · pit-timeline · pit-diff
  TRACE  bisect · explain · evidence
  recipes: `lawvm recipes`     ·     full command list below

examples (-j selects jurisdiction, default fi; Finnish IDs unless shown as ukpga/...):
  lawvm refs --to 2007/571          # what provisions cite this statute (reverse citation graph)
  lawvm cite 2009/738               # outgoing refs of a statute
  lawvm topic --topic kadmium       # full-text search across in-force sections
  lawvm oracle-text 1992/734 --section section:7a    # consolidated section text at current version
  lawvm uk-replay ukpga/2020/17     # UK: replay effects, compare vs published revised text
  lawvm uk-effects ukpga/2020/17    # UK: list/triage the effects recorded against a statute
"""

    _EPILOG = (
        "For 'what relates to / cites / nets X', try refs/cite/topic BEFORE reading sections. "
        "See: lawvm recipes"
    )

    parser = argparse.ArgumentParser(
        prog="lawvm",
        description=_CAPABILITY_MAP,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        parents=_P,
        help="inspect pipeline state at a named stage",
        description=(
            "Show statute state at a pipeline stage. "
            "Default (no --after): full replay body text for Finland; "
            "archive-backed source parse for UK IDs. "
            "--after parse: base statute structure. "
            "--after extract/normalize: ops from one amendment (requires --source)."
        ),
    )
    dump_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    dump_p.add_argument(
        "--after",
        choices=["parse", "extract", "normalize", "resolve", "apply"],
        help="pipeline stage to dump (default: apply for FI, source parse for UK)",
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
    dump_p.add_argument(
        "--db",
        metavar="PATH",
        help="UK farchive path for UK source-parse dumps (default: data/uk_legislation.farchive)",
    )
    dump_p.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable lawvm.dump.v1 JSON document for the apply (full "
        "replay) read: per-section text, content_hash, temporal version pin, and "
        "amending-act source attribution (FI replay-backed read only)",
    )
    dump_p.add_argument(
        "--hashes",
        action="store_true",
        help="append the short per-section content_hash to the human apply (full "
        "replay) output (display-only; FI replay-backed read only)",
    )
    dump_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        help="PIT date for --json/--hashes section selection (YYYY-MM-DD; "
        "default: replay cutoff)",
    )

    # --- source-dump ---
    source_dump_p = sub.add_parser(
        "source-dump",
        parents=_P,
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
    source_dump_p.add_argument(
        "--db",
        metavar="PATH",
        help="UK farchive path for UK source dumps (default: data/uk_legislation.farchive)",
    )

    # --- fi-source-label-audit ---
    fi_source_label_audit_p = sub.add_parser(
        "fi-source-label-audit",
        parents=_P,
        help="compare Finland source XML label normalization policies",
        description=(
            "Non-mutating audit for Finland source XML labels. Compares current "
            "candidate part/chapter/section label normalization policies and "
            "reports real source labels where they diverge. Does not affect replay."
        ),
    )
    fi_source_label_audit_p.add_argument(
        "statute_id",
        nargs="?",
        help="optional statute ID, e.g. 2006/1299; omit to scan a corpus prefix",
    )
    fi_source_label_audit_p.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="when statute_id is omitted, scan first N statutes sorted by ID (0 = all)",
    )
    fi_source_label_audit_p.add_argument(
        "--include-agreeing",
        action="store_true",
        help="emit rows even when all compared policies agree",
    )
    fi_source_label_audit_p.add_argument(
        "--examples",
        type=int,
        default=10,
        metavar="N",
        help="human output: max divergent example rows to print",
    )
    fi_source_label_audit_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- inspect-amendment ---
    inspect_amendment_p = sub.add_parser(
        "inspect-amendment",
        parents=_P,
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        choices=_INVARIANT_DETECTOR_CHOICES,
        help="structural detector to run (default: duplicate_label)",
    )
    diagnose_phase_p.add_argument(
        "--mode",
        default="legal_pit",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        choices=_INVARIANT_DETECTOR_CHOICES,
        help="structural detector to run (default: duplicate_label)",
    )
    invariant_bisect_p.add_argument(
        "--mode",
        default="legal_pit",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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

    # --- self-consistency ---
    self_consistency_p = sub.add_parser(
        "self-consistency",
        parents=_P,
        help="enumerate amendment-chain self-consistency violations across the corpus",
        description=(
            "Replay every curated statute in parallel and harvest every "
            "self-consistency signal: typed apply-failures, silently-swallowed "
            "target-absent ops, unhandled/dropped ops, source pathologies, "
            "skipped amendments, coverage gaps, structural invariant violations, "
            "replay lint warnings (flattened sublist / label-sequence gaps), "
            "and governed ELAB findings.  Grouped by signal type then category. "
            "Use -j uk / -j ee to route to the UK/EE harness (replay "
            "adjudications + compile rejections), or -j us for the U.S. federal "
            "amendatory-lowering audit (oracle-independent)."
        ),
    )
    self_consistency_p.add_argument(
        "--statutes",
        metavar="IDS",
        default="",
        help="comma-separated statute IDs to sweep (default: full curated corpus)",
    )
    self_consistency_p.add_argument(
        "--signal-types",
        dest="signal_types",
        metavar="TYPES",
        default="",
        help=(
            "comma-separated signal types to keep (default: all). Choices: "
            "apply_failure,target_absent,unhandled_op,source_pathology,"
            "skipped_amendment,coverage_gap,invariant_violation,"
            "invariant_lint_warning,elaboration_finding,occupancy_violation"
        ),
    )
    self_consistency_p.add_argument(
        "--corpus",
        metavar="PATH",
        default="",
        help=(
            "corpus CSV or plain-text statute-id list (default: bench_core subset; "
            "use --full for the full bench_corpus.csv)"
        ),
    )
    self_consistency_p.add_argument(
        "--full",
        action="store_true",
        help="sweep the full ~3545 curated corpus (bench_corpus.csv) instead of the ~690 bench_core subset",
    )
    self_consistency_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap the corpus to the first N statutes (default: no cap)",
    )
    self_consistency_p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="worker process count (default: cpu-2, capped at 8)",
    )
    self_consistency_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with the full signal rows",
    )
    # EE-only options (-j ee): the Estonia audit replays RT (base, oracle) pairs
    # from a curated corpus CSV against the Riigi Teataja Farchive.
    self_consistency_p.add_argument(
        "--db",
        default="",
        metavar="PATH",
        help="[-j ee] Riigi Teataja Farchive path (default: data/ee_riigiteataja.farchive)",
    )
    self_consistency_p.add_argument(
        "--ee-corpus",
        dest="ee_corpus",
        default="",
        metavar="CSV",
        help="[-j ee] curated EE corpus CSV (default: data/estonia/current_replayable_corpus.csv)",
    )
    self_consistency_p.add_argument(
        "--laws-only",
        dest="laws_only",
        action="store_true",
        help="[-j ee] restrict to Riigikogu laws (tyviseadus/muutmisseadus), excluding decrees",
    )
    # US-only option (-j us): the U.S. federal amendatory self-consistency audit
    # sweeps the bench-window public-law delta from the committed corpus CSV.
    self_consistency_p.add_argument(
        "--us-corpus",
        dest="us_corpus",
        default="",
        metavar="CSV",
        help="[-j us] committed US bench corpus CSV (default: us/bench/us_bench_corpus.csv)",
    )

    # --- snapshot-debug ---
    snapshot_debug_p = sub.add_parser(
        "snapshot-debug",
        parents=_P,
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        parents=_P,
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        "--temporal-labels",
        dest="temporal_labels",
        action="store_true",
        help=(
            "label each span of the section as [IN FORCE] / [ENTERS FORCE <date>] / "
            "[SUPERSEDED (aiempi sanamuoto)] / [NOTE] using the structural "
            "amendment-version markers in the consolidated XML. The default "
            "flattened 'Full text' output is unchanged."
        ),
    )
    oracle_text_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )
    oracle_text_p.add_argument(
        "--no-hints",
        dest="no_hints",
        action="store_true",
        help="suppress point-of-use discovery hints on stderr (also: LAWVM_NO_HINTS=1)",
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode used to prepare the plan (default: official_consolidation)",
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
        parents=_P,
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        parents=_P,
        help="typed replay-vs-oracle classification for one statute",
        description=(
            "Public one-statute wrapper over oracle-check classification. "
            "Shows section diagnoses, source pathologies, and contingent effective-date sources."
        ),
    )
    classify_p.add_argument("statute_id", help="statute ID, e.g. 2006/1299")
    classify_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
    )
    classify_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- bench ---
    # Inlined from lawvm.tools.bench.register_cli to avoid importing bench.py
    # at parser-build time (bench → grafter → lxml/icontract/johtolause = ~374 ms).
    # Dispatch in main() still imports bench lazily; this is pure argparse only.
    bench_p = sub.add_parser(
        "bench",
        parents=_P,
        help="corpus benchmark with history",
        description=(
            "Run full corpus benchmark and record results. Tracks score trajectory over time and detects regressions."
        ),
    )
    bench_p.add_argument(
        "--label",
        metavar="LABEL",
        help="tag for this run, e.g. v22 (default: auto-generated timestamp)",
    )
    bench_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help=(
            "replay mode: official_consolidation (default) compares against the Finlex consolidated XML; "
            "legal_pit applies date-cutoff PIT materialization (excludes future-dated amendments "
            "and corrigendum patches, giving a cleaner accuracy signal against the legal record)"
        ),
    )
    bench_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="path to corpus CSV (default: data/finland/bench_corpus.csv; fallback: .tmp/batch_test_list.csv)",
    )
    bench_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="number of worst statutes to report (default: 20)",
    )
    bench_p.add_argument(
        "--history",
        action="store_true",
        help="show score trajectory from benchmark_history.csv",
    )
    bench_p.add_argument(
        "--regressions",
        action="store_true",
        help="show statutes that regressed vs previous run",
    )
    bench_p.add_argument(
        "--compare",
        nargs=2,
        metavar=("LABEL_A", "LABEL_B"),
        help="compare two labeled runs",
    )
    bench_p.add_argument(
        "--show",
        metavar="LABEL",
        help="show worst performers from a past labeled run (no re-run needed)",
    )
    bench_p.add_argument(
        "--filter-live",
        dest="filter_live",
        action="store_true",
        help="skip statutes whose consolidated oracle is contentAbsent (repealed/expired)",
    )
    bench_p.add_argument(
        "--filter-repealed",
        dest="filter_repealed",
        action="store_true",
        help="skip statutes where ≥50%% of oracle sections are kumottu (L:lla/A:lla) "
        "(individually-repealed statutes whose oracle is just repeal annotations)",
    )
    bench_p.add_argument(
        "--filter-empty",
        dest="filter_empty",
        action="store_true",
        help="skip statutes where oracle appears silently-emptied: ≤3 sections, "
        "0 kumottu annotations, <2000 bytes of body text",
    )
    bench_p.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help=(
            "parallel workers (FI default: min(16, cpu_count); UK/EE default: "
            "min(cpu_count, 8)); per-worker peak RSS ~860 MB after source-root "
            "eviction — heavy lanes still serialize via memory guard)"
        ),
    )
    bench_p.add_argument(
        "--by-decade",
        dest="by_decade",
        action="store_true",
        help="show score breakdown grouped by enactment decade (use with --show or live run)",
    )
    bench_p.add_argument(
        "--filter-decade",
        dest="filter_decade",
        metavar="DECADE",
        help="restrict corpus to statutes from DECADE (e.g. '1980s', '1990s')",
    )
    bench_p.add_argument(
        "--filter-zero-amend",
        dest="filter_zero_amend",
        action="store_true",
        help="keep only statutes with 0 amendments (isolates XML format failures from PEG failures)",
    )
    bench_p.add_argument(
        "--filter-nonzero-amend",
        dest="filter_nonzero_amend",
        action="store_true",
        help="keep only statutes with ≥1 amendment (focus on PEG/grafter accuracy)",
    )
    bench_p.add_argument(
        "--corpus-stats",
        dest="corpus_stats",
        action="store_true",
        help="print corpus statistics by decade (N statutes, amendment distribution) without running the benchmark",
    )
    bench_p.add_argument(
        "--source-closure-stats",
        dest="source_closure_stats",
        action="store_true",
        help=(
            "[-j uk --corpus-stats] also inspect replay-required affecting-act "
            "XML closure from the archive; slower than header-only corpus stats"
        ),
    )
    bench_p.add_argument(
        "--diagnose",
        action="store_true",
        help="with --show: classify failure modes for worst performers "
        "(KUMOTTU_ORACLE / UNCOVERED_INSERT / EXTRA_REPLAY / CONTENT_DRIFT / EMPTY_ORACLE)",
    )
    bench_p.add_argument(
        "--diagnostic-replay",
        action="store_true",
        help="use full replay materialization and replay notices instead of the default fast bench replay",
    )
    bench_p.add_argument(
        "--db",
        metavar="PATH",
        help="[-j ee/-j uk] Farchive DB path",
    )
    bench_p.add_argument(
        "--include-decrees",
        action="store_true",
        default=True,
        dest="include_decrees",
        help="[-j ee] include decree groups in addition to laws (default)",
    )
    bench_p.add_argument(
        "--laws-only",
        action="store_false",
        dest="include_decrees",
        help="[-j ee] restrict Estonia corpus loading to law schemas",
    )
    bench_p.add_argument(
        "--ee-corpus",
        metavar="CSV_PATH",
        dest="ee_corpus",
        help="[-j ee] path to corpus CSV (default: data/estonia/current_replayable_corpus.csv)",
    )
    bench_p.add_argument(
        "--reindex",
        action="store_true",
        help="[-j ee] force live re-index of the RT archive instead of reading corpus CSV",
    )
    bench_p.add_argument(
        "--statute",
        metavar="ID",
        help="run bench for a single statute ID (FI/EE/UK)",
    )
    bench_p.add_argument(
        "--types",
        nargs="+",
        metavar="TYPE",
        help="[-j uk] act types to include (default: ukpga asp asc nia)",
    )
    bench_p.add_argument(
        "--corpus-csv",
        action="store_true",
        dest="corpus_csv",
        help="[-j uk] build/refresh data/uk/bench_corpus.csv from archive and exit",
    )
    bench_p.add_argument(
        "--curate-corpus",
        metavar="CSV_PATH",
        help="[-j uk] write a source-complete curated corpus CSV and exit",
    )
    bench_p.add_argument(
        "--curate-preset",
        choices=[
            "canary",
            "tight",
            "stress",
            "modern-canary",
            "modern-tight",
            "hard-canary",
            "hard-tight",
            "hard-stress",
        ],
        help=(
            "[-j uk] curated corpus preset: canary=40, tight=200, stress=400, "
            "modern-canary=40, modern-tight=200, hard-canary=40, hard-tight=200, "
            "hard-stress=400. Hard presets require source-complete effectful rows "
            "and prefer heavier replay rows within each stratum. "
            "If --curate-corpus is omitted, writes the standard data/uk preset CSV"
        ),
    )
    bench_p.add_argument(
        "--curate-size",
        type=int,
        default=None,
        metavar="N",
        help="[-j uk --curate-corpus] maximum curated rows to write (default: preset size or 200)",
    )
    bench_p.add_argument(
        "--curate-require-source-closure",
        dest="curate_require_source_closure",
        action="store_true",
        help=(
            "[-j uk --curate-corpus] only curate rows whose replay-required "
            "affecting-act XML closure is full, or not required"
        ),
    )
    bench_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="[-j uk] process only first N statutes (for quick smoke tests)",
    )
    bench_p.add_argument(
        "--no-save",
        action="store_true",
        dest="no_save",
        help="print a bench report without writing run CSV/history artifacts",
    )
    bench_p.add_argument(
        "--summary-only",
        action="store_true",
        dest="summary_only",
        help="[-j uk] print bounded headline metrics instead of the full detailed report",
    )
    bench_p.add_argument(
        "--replay",
        action="store_true",
        help="[-j uk] also run amendment replay and report replayed vs enacted EID scores",
    )
    bench_p.add_argument(
        "--replay-adjudication-samples",
        nargs="+",
        metavar="KIND",
        help="[-j uk --replay] print bounded sample rows for selected replay adjudication kinds",
    )
    bench_p.add_argument(
        "--replay-adjudication-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="[-j uk --replay] samples per selected replay adjudication kind (default: 5)",
    )
    bench_p.add_argument(
        "--diagnostic-sample-lane",
        metavar="LANE",
        help=(
            "[-j uk --show] stream sample rows from a bench diagnostics sidecar "
            "for one lane, e.g. source_acquisition or lowering"
        ),
    )
    bench_p.add_argument(
        "--diagnostic-sample-rule",
        metavar="RULE_ID",
        help="[-j uk --show --diagnostic-sample-lane] restrict samples to one rule_id",
    )
    bench_p.add_argument(
        "--diagnostic-sample-pattern",
        metavar="PATTERN",
        help=(
            "[-j uk --show --diagnostic-sample-lane] restrict samples to one "
            "extracted source-preview pattern"
        ),
    )
    bench_p.add_argument(
        "--diagnostic-sample-blocking",
        action="store_true",
        help="[-j uk --show --diagnostic-sample-lane] only sample blocking diagnostics",
    )
    bench_p.add_argument(
        "--diagnostic-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="[-j uk --show --diagnostic-sample-lane] maximum sidecar samples to print (default: 5)",
    )
    bench_p.add_argument(
        "--diagnostic-pattern-summary",
        action="store_true",
        help=(
            "[-j uk --show --diagnostic-sample-lane] group matched diagnostics "
            "by extracted source-preview pattern"
        ),
    )
    add_uk_replay_regime_arguments(bench_p, help_prefix="[-j uk --replay]")
    bench_p.add_argument(
        "--no-commencement",
        action="store_true",
        dest="no_commencement",
        help="[-j uk] disable commencement filtering (on by default; use to compare raw EID scores)",
    )
    bench_p.add_argument(
        "--phase-timings",
        action="store_true",
        dest="phase_timings",
        help="[-j uk] print measured per-row phase timings for replay performance triage",
    )
    bench_p.add_argument(
        "--no-text-scores",
        action="store_true",
        dest="no_text_scores",
        help="skip diagnostic Levenshtein text similarity scoring for faster corpus sweeps",
    )
    bench_p.add_argument(
        "--worker-max-tasks",
        type=int,
        default=None,
        metavar="N",
        help=(
            "[-j uk] recycle each parallel worker after N statutes to cap long-run "
            "worker RSS growth; slower, but useful for WSL2/full-corpus replay sweeps"
        ),
    )
    bench_p.add_argument(
        "--min-year",
        type=int,
        metavar="YEAR",
        help="[-j uk] only include statutes from this year onward",
    )
    bench_p.add_argument(
        "--max-year",
        type=int,
        metavar="YEAR",
        help="[-j uk] only include statutes up to this year",
    )
    bench_p.add_argument(
        "--html-summary",
        dest="html_summary",
        action="store_true",
        help="after the bench run, compare corpus oracle section counts against the "
        "HTML oracle cache (from farchive) to quantify stale-oracle "
        "impact on bench scores",
    )
    bench_p.add_argument(
        "--oracle-aware-headline",
        dest="oracle_aware_headline",
        action="store_true",
        help="add an oracle-stale-aware headline mean that excludes statutes "
        "classified as ORACLE_STALE by oracle-check; raw scores remain unchanged",
    )
    bench_p.add_argument(
        "--section-score",
        dest="section_score",
        action="store_true",
        help="compute per-section Levenshtein similarity in addition to full-text "
        "score; reports mean section accuracy vs full-text accuracy and adds "
        "section_similarity column to the run CSV",
    )
    bench_p.add_argument(
        "--warm-oracle",
        dest="warm_oracle",
        action="store_true",
        help="before running, pre-fetch API PITs for statutes that lack a versioned "
        "oracle (fin@YYYYNNNN) in the corpus store; requires data/finlex.farchive; "
        "rate-limited at ~1 req/sec",
    )
    bench_p.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "[-j nz] use the curated smoke corpus (data/nz/bench_corpus_smoke.csv) "
            "instead of the full corpus for a quick run"
        ),
    )
    bench_p.add_argument(
        "--max-works",
        type=int,
        default=None,
        dest="max_works",
        metavar="N",
        help="[-j nz] score only the first N works of the corpus (stride for quick runs)",
    )
    bench_p.add_argument(
        "--json",
        action="store_true",
        help="[-j nz] emit the multi-lane bench report as JSON to stdout",
    )
    bench_p.add_argument(
        "--output-json",
        dest="output_json",
        metavar="PATH",
        help="[-j nz] write the multi-lane bench report JSON to PATH",
    )

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
        "--provision",
        dest="address",
        metavar="ADDR",
        help="alias for --address, matching provision-state",
    )
    blame_p.add_argument(
        "--source",
        metavar="AMENDMENT_ID",
        help="only show provisions last-touched by this amendment",
    )
    blame_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
    )
    blame_p.add_argument(
        "--format",
        default="text",
        choices=["text", "json"],
        help=(
            "output format (default: text). 'json' emits per-address rows with a"
            " typed status enum (unmodified_base_text | modified_by_op |"
            " op_unapplied_or_engine_error | address_unresolved)."
        ),
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
        parents=_P,
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
            "May produce a lower score than quirks mode, where recoveries can proceed with evidence."
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        parents=_P,
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        "--failed-only",
        action="store_true",
        help="print only filtered replay failed operations and counts",
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
        parents=_P,
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
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        parents=_P,
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
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

    # --- provision-state ---
    provision_state_p = sub.add_parser(
        "provision-state",
        parents=_P,
        help="stable PIT provision-state JSON seam output",
        description=(
            "Resolve one provision at one point in time and emit a stable JSON "
            "state pin for consumers such as MeVM. Currently backed by Finnish "
            "live replay timelines."
        ),
    )
    provision_state_p.add_argument("statute_id", help="statute ID, e.g. 2009/953")
    provision_state_p.add_argument(
        "--provision",
        "--address",
        dest="provision",
        required=True,
        metavar="ADDR",
        help=(
            "provision address, e.g. 'section:4' or 'chapter:1/section:4' "
            "(--address is an alias)"
        ),
    )
    provision_state_p.add_argument(
        "--as-of",
        required=True,
        metavar="DATE",
        help="PIT date, e.g. '2024-01-01'",
    )
    provision_state_p.add_argument(
        "--query-type",
        metavar="TYPE",
        default="governing",
        choices=["governing", "in_force"],
        help=(
            "PIT query semantics: 'governing' (default) includes retroactive amendments; "
            "'in_force' returns only what was enacted by that date"
        ),
    )
    provision_state_p.add_argument(
        "--territory",
        metavar="TERRITORY",
        help="explicit territory/scope selector when timeline selection requires it",
    )
    provision_state_p.add_argument(
        "--include-ir",
        action="store_true",
        help="include structured IRNode JSON for the selected provision",
    )

    # --- read (clean L1 analyst reading surface) ---
    read_p = sub.add_parser(
        "read",
        parents=_P,
        help="clean point-in-time in-force reading surface (replay-L1)",
        description=(
            "Read a provision in force on a date, cruft-free. With a §-selector "
            "(e.g. §3:1 or §3:1.2) it resolves replay-L1 via provision-state; with "
            "no selector it replays the whole statute. --raw drills down to the L0 "
            "consolidated prose at the SAME selector; --xml to the raw source XML. "
            "--json returns the stable lawvm.provision_state.v1 pin unchanged."
        ),
    )
    read_p.add_argument("statute_id", help="statute ID, e.g. 2011/805")
    read_p.add_argument(
        "selector",
        nargs="?",
        default="",
        help="provision selector, e.g. '§3:1', '§3:1.2', '§7'; omit for whole statute",
    )
    read_p.add_argument(
        "--as-of", dest="as_of", metavar="DATE",
        help="PIT date (default: today)",
    )
    read_p.add_argument(
        "--query-type", dest="query_type", default="in_force",
        choices=["governing", "in_force"],
        help="PIT query semantics (default: in_force)",
    )
    read_p.add_argument("--territory", dest="territory", metavar="TERRITORY")
    read_p.add_argument(
        "--include-ir", dest="include_ir", action="store_true",
        help="include structured IRNode JSON (--json only)",
    )
    read_p.add_argument(
        "--raw", action="store_true",
        help="drill down to L0 consolidated prose at the same selector (oracle-text)",
    )
    read_p.add_argument(
        "--temporal-labels", dest="temporal_labels", action="store_true",
        help="with --raw: segment the L0 prose into IN_FORCE/SUPERSEDED/... spans",
    )
    read_p.add_argument(
        "--subsections", action="store_true",
        help="with --raw: include per-subsection breakdown",
    )
    read_p.add_argument(
        "--xml", action="store_true",
        help="drill down to raw archived source XML at the same selector (source-dump)",
    )
    read_p.add_argument("--json", action="store_true", help="emit JSON")

    # --- reconcile (replay-L1 vs oracle-L1; divergence is the signal) ---
    reconcile_p = sub.add_parser(
        "reconcile",
        parents=_P,
        help="diff replay-L1 vs oracle-L1 at a selector; flag divergence",
        description=(
            "Compute two independent clean in-force-at-D views — replay-L1 "
            "(provision-state) and oracle-L1 (consolidated, structurally "
            "segmented) — and compare. AGREE shows one view; DISAGREE shows BOTH "
            "and classifies the cause (temporal / editorial / presence). "
            "Divergence is the signal and is never silently resolved."
        ),
    )
    reconcile_p.add_argument(
        "statute_id", nargs="?", default="",
        help="statute ID, e.g. 2011/805 (omit with --sweep)",
    )
    reconcile_p.add_argument(
        "selector", nargs="?", default="",
        help="provision selector, e.g. '§3:1'; omit to scan the whole statute",
    )
    reconcile_p.add_argument(
        "--as-of", dest="as_of", metavar="DATE", help="PIT date (default: today)",
    )
    reconcile_p.add_argument(
        "--query-type", dest="query_type", default="in_force",
        choices=["governing", "in_force"],
        help="PIT query semantics (default: in_force)",
    )
    reconcile_p.add_argument(
        "--at-amendment", dest="at_amendment", metavar="ID",
        help="pin the oracle consolidated version to this amendment",
    )
    reconcile_p.add_argument("--json", action="store_true", help="emit JSON")
    # --- corpus-wide sweep mode (the self-audit) ---
    reconcile_p.add_argument(
        "--sweep", action="store_true",
        help="corpus-wide replay-L1 vs oracle-L1 self-audit; writes a ranked report",
    )
    reconcile_p.add_argument(
        "--sample", type=int, metavar="N",
        help="(--sweep) reconcile the top-N statutes by amendment count",
    )
    reconcile_p.add_argument(
        "--all", action="store_true",
        help="(--sweep) reconcile the full corpus (slow; explicit opt-in)",
    )
    reconcile_p.add_argument(
        "--min-amendments", dest="min_amendments", type=int, default=1, metavar="N",
        help="(--sweep) only statutes with >= N amendments (default: 1)",
    )
    reconcile_p.add_argument(
        "--max-sections", dest="max_sections", type=int, metavar="N",
        help="(--sweep) cap sections reconciled per statute (debug)",
    )
    reconcile_p.add_argument(
        "--statute", action="append", default=[], metavar="ID",
        help="(--sweep) reconcile these explicit statute IDs (repeatable)",
    )
    reconcile_p.add_argument(
        "--label", dest="label", metavar="LABEL",
        help="(--sweep) report label (default: sweep_<as-of>)",
    )
    reconcile_p.add_argument(
        "--out-dir", dest="out_dir", metavar="DIR", default="reports",
        help="(--sweep) report output directory (default: reports)",
    )
    reconcile_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="(--sweep) per-statute progress to stderr",
    )

    # --- provenance / trace (wording -> amendment -> HE/preparatory chain) ---
    for command_name, command_help in (
        ("provenance", "trace in-force wording to amendment, HE, and preparatory refs"),
        ("trace", "alias for provenance"),
    ):
        provenance_p = sub.add_parser(
            command_name,
            parents=_P,
            help=command_help,
            description=(
                "Resolve one §-selector through replay-L1, then surface the source "
                "amendment, originating HE, committee/parliament preparatory refs, "
                "and fixed-date commencement gate. This assembles existing engines "
                "and does not introduce a new replay or parsing kernel."
            ),
        )
        provenance_p.add_argument("statute_id", help="statute ID, e.g. 2011/805")
        provenance_p.add_argument(
            "selector", nargs="?", default="",
            help="provision selector, e.g. '§3:1'",
        )
        provenance_p.add_argument(
            "--as-of", dest="as_of", metavar="DATE", help="PIT date (default: today)",
        )
        provenance_p.add_argument(
            "--query-type", dest="query_type", default="in_force",
            choices=["governing", "in_force"],
            help="PIT query semantics (default: in_force)",
        )
        provenance_p.add_argument(
            "--data-dir", dest="data_dir", metavar="PATH", default="data/fi/v1",
            help="projection directory for fi_he_corpus.parquet (default: data/fi/v1)",
        )
        provenance_p.add_argument("--json", action="store_true", help="emit JSON")
        provenance_p.add_argument(
            "--hyperlinks",
            choices=["auto", "always", "never"],
            default="auto",
            help=(
                "OSC 8 terminal hyperlinks on legislative refs in HUMAN output "
                "(auto = only on a TTY; never emitted into JSON/non-tty)"
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

    branch_demo_p = sub.add_parser(
        "branch-demo",
        help="emit a synthetic branch/authority demo payload",
        description=(
            "Emit a small synthetic enacted-vs-proposal payload showing that "
            "non-enacted branch operations remain outside the default enacted lane."
        ),
    )
    branch_demo_p.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON output",
    )

    uk_branch_demo_p = sub.add_parser(
        "uk-branch-demo",
        help="emit a UK proposed-law branch graph demo payload",
        description=(
            "Emit a small UK-shaped proposed-law branch payload. The payload is "
            "graph-only and proves proposed operations remain outside the "
            "default enacted replay lane."
        ),
    )
    uk_branch_demo_p.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON output",
    )

    uk_branch_import_p = sub.add_parser(
        "uk-branch-import",
        help="import an explicit UK proposed-law branch graph JSON payload",
        description=(
            "Import a structured UK proposed-law payload into the branch graph. "
            "This is not a bill parser; it preserves externally owned proposed "
            "claims outside the default enacted replay lane."
        ),
    )
    uk_branch_import_p.add_argument("input", help="JSON payload path")
    uk_branch_import_p.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON output",
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
    # Inlined from lawvm.tools.corrigendum.register_cli to avoid importing corrigendum.py
    # at parser-build time (corrigendum → aiohttp ~111 ms + lawvm.finland.proof_surfaces ~26 ms).
    # Dispatch in main() still imports corrigendum lazily; this is pure argparse only.
    corr_p = sub.add_parser(
        "corrigendum",
        help="corrigendum (oikaisu) status, inspection, and LLM classification",
        description=(
            "Inspect legally binding corrections (corrigenda) to published statutes. "
            "Subcommands: status [SID], apply SID, classify, report."
        ),
    )
    corr_sub = corr_p.add_subparsers(dest="corrigendum_command", metavar="<subcommand>")

    corr_status_p = corr_sub.add_parser(
        "status",
        help="corpus-wide summary or single-statute corrigendum details",
    )
    corr_status_p.add_argument(
        "statute_id", nargs="?",
        help="statute ID (e.g. 2007/26) — omit for corpus summary",
    )

    corr_apply_p = corr_sub.add_parser(
        "apply",
        help="extract corrigendum PDF(s) and show text via pdftotext",
    )
    corr_apply_p.add_argument("statute_id", help="statute ID, e.g. 2007/26")
    corr_apply_p.add_argument(
        "--save", metavar="PATH",
        help="save extracted PDF to this path",
    )

    corr_classify_p = corr_sub.add_parser(
        "classify",
        help="LLM-classify corrigendum PDFs into typed corrections (johtolause/table/prose/…)",
        description=(
            "Run all Finnish (sk*) corrigendum PDFs through a local LLM to extract "
            "typed correction records. Results are synced into the git-tracked "
            "data/finland/corrigendum_official_fi.jsonl and "
            "data/finland/corrigendum_adjudications_fi.jsonl corpora "
            "(with sqlite kept only as a transitional scratch artifact). "
            "Johtolause corrections are source-verified against the corpus store. "
            "Idempotent — already-classified PDFs are skipped unless --rerun."
        ),
    )
    corr_classify_p.add_argument(
        "--lang", choices=["fi", "sv", "all"], default="fi",
        help="language filter: fi=sk* (default), sv=fs*, all=both",
    )
    corr_classify_p.add_argument(
        "--type", metavar="TYPE",
        help="after classification, show only this correction type (e.g. johtolause)",
    )
    corr_classify_p.add_argument(
        "--parallel", type=int, default=None, metavar="N",
        help="concurrent LLM calls (default: cpu_count)",
    )
    corr_classify_p.add_argument(
        "--limit", type=int, metavar="N",
        help="process at most N PDFs (for testing)",
    )
    corr_classify_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="run LLM extraction but do not write to DB",
    )
    corr_classify_p.add_argument(
        "--rerun", action="store_true",
        help="re-classify already-classified PDFs (overwrite)",
    )
    corr_classify_p.add_argument(
        "--compare", action="store_true",
        help="run both regex and LLM; log divergences; write regex result (implies --rerun for comparison scope)",
    )
    corr_classify_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print result for every PDF (default: only johtolause cases)",
    )

    corr_check_p = corr_sub.add_parser(
        "check-patches",
        help="audit patch hit/miss/ambig rates across corpus; write misapplied JSONL",
    )
    corr_check_p.add_argument(
        "--out", metavar="PATH",
        help="output path for misapplied JSONL (default: data/finland/corrigendum_misapplied_fi.jsonl)",
    )
    corr_check_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print first 20 misapplied records",
    )
    corr_check_p.add_argument(
        "--workers", "-j", type=int, default=8, metavar="N",
        help="parallel worker threads (default: 8)",
    )

    corr_compl_p = corr_sub.add_parser(
        "check-completeness",
        help="report PDFs where expected_pair_count exceeds extracted record count",
    )
    corr_compl_p.add_argument(
        "--db", metavar="PATH",
        help="path to official records JSONL (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_compl_p.add_argument(
        "--json", action="store_true",
        help="output as JSON array",
    )

    corr_recomp_p = corr_sub.add_parser(
        "recompute-completeness",
        help="refresh expected_pair_count in JSONL from regex (no LLM)",
    )
    corr_recomp_p.add_argument(
        "--db", metavar="PATH",
        help="official JSONL path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_recomp_p.add_argument(
        "--dry-run", action="store_true",
        help="compute counts but do not write JSONL",
    )
    corr_recomp_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print each PDF whose count changes",
    )

    corr_verify_p = corr_sub.add_parser(
        "verify",
        help="re-run source verification for classified corrections (no LLM needed)",
        description=(
            "Update verified_in_source column without re-running LLM classification. "
            "Use after: fixing _verify_in_source bugs, updating the corpus store, etc."
        ),
    )
    corr_verify_p.add_argument(
        "--type", metavar="TYPE", default="johtolause",
        help="correction type to verify (default: johtolause)",
    )
    corr_verify_p.add_argument(
        "--amendment", metavar="AMENDMENT_ID", dest="amendment_id",
        help="restrict verification to one amendment (e.g. 1246/2002)",
    )

    corr_report_p = corr_sub.add_parser(
        "report",
        help="query classified corrigendum results from the text corpus",
        description=(
            "Print classified correction records from the git-tracked "
            "corrigendum text corpus. "
            "Filter by type, amendment, or verified status."
        ),
    )
    corr_report_p.add_argument(
        "--type", metavar="TYPE",
        help="filter by correction type (johtolause|table|footnote|prose|metadata|unknown)",
    )
    corr_report_p.add_argument(
        "--amendment", metavar="AMENDMENT_ID", dest="amendment_id",
        help="filter to one amendment (e.g. 984/2018)",
    )
    corr_report_p.add_argument(
        "--verified", action="store_true",
        help="only show corrections verified in the corpus store",
    )

    corr_test_p = corr_sub.add_parser(
        "test",
        help="dry-run patch application for one amendment — shows what would change",
        description=(
            "Load classified patches for an amendment, apply them to the source XML "
            "from the corpus store, and show pass/fail + before/after context for each patch. "
            "Useful for debugging why a corrigendum patch does or doesn't match."
        ),
    )
    corr_test_p.add_argument(
        "amendment_id",
        help="amendment ID to test (NUM/YEAR or YEAR/NUM, e.g. '984/2018' or '2018/984')",
    )

    corr_diffpdf_p = corr_sub.add_parser(
        "diff-pdf",
        help="diff PDF vs XML text for corrigendum-affected amendments (ground-truth validation)",
        description=(
            "For each amendment in the classified corrigendum corpus, extract the preamble text from "
            "both the PDF and the XML in the corpus store, and compare them. PDFs have corrigenda "
            "applied; XMLs do not — so diffs reveal corrections not yet in the patch pipeline. "
            "Output: .tmp/pdf_xml_diffs.jsonl with one record per amendment."
        ),
    )
    corr_diffpdf_p.add_argument(
        "--output", "-o", metavar="FILE",
        help="output JSONL file (default: .tmp/pdf_xml_diffs.jsonl)",
    )
    corr_diffpdf_p.add_argument(
        "--limit", type=int, metavar="N",
        help="process only first N amendments (for testing)",
    )
    corr_diffpdf_p.add_argument(
        "--workers", type=int, default=8, metavar="N",
        help="parallel workers for pdftotext (default: 8)",
    )
    corr_diffpdf_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print each amendment with a diff",
    )
    corr_diffpdf_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_reex_p = corr_sub.add_parser(
        "reextract",
        help="LLM-assisted reextraction for no-match patches (gives LLM both PDF + XML context)",
        description=(
            "For each patch where wrong_text doesn't match the amendment XML, calls the local "
            "LLM with both the corrigendum PDF text and the amendment XML. The LLM finds the "
            "exact bytes in the XML to replace. Use --update to apply changes and resync "
            "the git-tracked corrigendum corpus."
        ),
    )
    corr_reex_p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="process at most N no-match patches (default: all)",
    )
    corr_reex_p.add_argument(
        "--update", action="store_true",
        help="write verified improvements back to the official corrigendum text corpus",
    )
    corr_reex_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="show LLM output for all patches including failures",
    )

    corr_manual_p = corr_sub.add_parser(
        "manual-template",
        help="emit YAML scaffold entries for corrigendum_manual.yaml from classified patches",
        description=(
            "Load one amendment's classified corrigendum items from the git-tracked "
            "corrigendum corpus, "
            "filter to the items that still do not match source XML by default, and emit "
            "a ready-to-paste YAML scaffold for corrigendum_manual.yaml."
        ),
    )
    corr_manual_p.add_argument(
        "amendment_id", metavar="AMENDMENT_ID",
        help="corrected amendment id in NUM/YEAR format, e.g. 991/2012",
    )
    corr_manual_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_manual_p.add_argument(
        "--all", action="store_true",
        help="include all fi correction items for this amendment, not just current no-match items",
    )
    corr_manual_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of YAML",
    )

    corr_open_manual_p = corr_sub.add_parser(
        "open-manual",
        help="list current live manual-corrigendum candidates",
        description=(
            "Scan high-no-match Finnish corrigendum amendments and recompute "
            "current manual-template viability, separating real open manual "
            "items from attachment-only and already-covered cases."
        ),
    )
    corr_open_manual_p.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="inspect at most N amendments with unverified classified items (default: 20)",
    )
    corr_open_manual_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_open_manual_p.add_argument(
        "--all", action="store_true",
        help="include attachment-only and already-covered amendments in the output",
    )
    corr_open_manual_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )
    corr_open_manual_p.add_argument(
        "--proof-report", action="store_true",
        help="emit a bundled JSON proof-surface report; legacy --json remains a row list",
    )

    corr_overview_p = corr_sub.add_parser(
        "overview",
        help="summarize corpus-wide corrigendum adjudication state",
        description=(
            "Build a corpus-level view over official corrigendum items, current "
            "verification/adjudication status, and the top amendments that still "
            "look open or attachment-only."
        ),
    )
    corr_overview_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_overview_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="show at most N amendments in each top-list bucket (default: 10)",
    )
    corr_overview_p.add_argument(
        "--live", action="store_true",
        help="recompute unresolved item status against source XML instead of relying on stored adjudications",
    )
    corr_overview_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_sources_p = corr_sub.add_parser(
        "sources",
        help="inspect or rebuild the PDF-level corrigendum provenance manifest",
        description=(
            "Build or inspect the git-tracked one-record-per-PDF provenance "
            "manifest for official Finnish corrigendum PDFs."
        ),
    )
    corr_sources_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_sources_p.add_argument(
        "--refresh", action="store_true",
        help="rebuild data/finland/corrigendum_sources_fi.jsonl from the official corrigendum corpus",
    )
    corr_sources_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="show at most N source records (default: 10; <=0 shows all)",
    )
    corr_sources_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_backfill_meta_p = corr_sub.add_parser(
        "backfill-meta",
        help="backfill missing official corrigendum amendment/date metadata from XML refs",
        description=(
            "Use authoritative <finlex:corrigendum> blocks from the consolidated "
            "oracle XML to fill missing amendment ids and publish dates in the "
            "official corrigendum corpus."
        ),
    )
    corr_backfill_meta_p.add_argument(
        "--db", metavar="PATH",
        help="official corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_backfill_meta_p.add_argument(
        "--update", action="store_true",
        help="write backfilled metadata into the official corrigendum JSONL",
    )
    corr_backfill_meta_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_prov_p = corr_sub.add_parser(
        "provenance",
        help="show one amendment's official items, verification state, and manual coverage together",
        description=(
            "Build an amendment-scoped operator view over official corrigendum items, "
            "current source verification, and manual override coverage so each "
            "corrigendum item can be audited in one place."
        ),
    )
    corr_prov_p.add_argument(
        "amendment_id", metavar="AMENDMENT_ID",
        help="corrected amendment id in NUM/YEAR format, e.g. 442/2016",
    )
    corr_prov_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_prov_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_review_p = corr_sub.add_parser(
        "review",
        help="review one statute's live oracle disagreements against corrigendum evidence",
        description=(
            "Run live oracle disagreement classification for one statute and group "
            "diverging sections by blamed amendment, then overlay existing "
            "classified corrigendum items and manual-override counts for those amendments."
        ),
    )
    corr_review_p.add_argument("statute_id", help="statute ID, e.g. 1995/1552")
    corr_review_p.add_argument(
        "--mode", default="legal_pit",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode for live disagreement classification (default: legal_pit)",
    )
    corr_review_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_review_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

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
            "affected statutes, and proof/frontier lanes in detail mode."
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
        help="categorize each failure by replay/root-cause category and proof/frontier lane",
    )
    failures_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable output for --detail",
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
        "--witness-attribution-json",
        dest="witness_attribution_json",
        action="store_true",
        help=(
            "emit a read-only per-compiled-op effect-feed witness attribution "
            "surface (op -> witness_rule_id, action family, owning phase, source "
            "effect-row + affecting-act fragment locator, adjudication bucket) as "
            "JSON and exit; does not change replay"
        ),
    )
    uk_replay_p.add_argument(
        "--commencement",
        action="store_true",
        help=(
            "also compute the symmetric commenced-EID comparison lane used by "
            "UK bench; raw EID comparison remains the default headline"
        ),
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

    # --- uk-acquire ---
    uk_acquire_p = sub.add_parser(
        "uk-acquire",
        help="download enacted XML, current XML, and effects feed for one UK statute",
        description=(
            "Single-statute UK acquisition/debug surface.  Fetches primary "
            "source artifacts for one UK statute into the Farchive DB: enacted "
            "XML (immutable, stored once), current XML (slow-mutable, "
            "TTL-governed), and effects feed pages (slow-mutable, "
            "TTL-governed).  Use --affecting to also pre-fetch missing "
            "affecting act XMLs.  Use --dry-run to preview without downloading "
            "anything.  For full-corpus orchestration, use lawvm uk-corpus all; "
            "both surfaces share UK source-state and Multiple Choices rules."
        ),
    )
    uk_acquire_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/2020/17",
    )
    uk_acquire_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_acquire_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print what would be fetched without downloading anything",
    )
    uk_acquire_p.add_argument(
        "--enacted-only",
        dest="enacted_only",
        action="store_true",
        help="only fetch enacted XML; skip current XML and effects feed",
    )
    uk_acquire_p.add_argument(
        "--affecting",
        action="store_true",
        help="also pre-fetch missing affecting act XMLs (like uk-fetch-affecting)",
    )
    uk_acquire_p.add_argument(
        "--force-refresh",
        dest="force_refresh",
        action="store_true",
        help="re-fetch mutable resources (current XML, effects feed) even if TTL says fresh",
    )
    uk_acquire_p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECS",
        help="seconds between HTTP requests (default: 0.5)",
    )
    uk_acquire_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print a line for each resource fetched or skipped",
    )
    uk_acquire_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON acquisition report",
    )

    # --- uk-corpus (native corpus sync; harmonized with ee-corpus / nz-corpus) ---
    uk_corpus_p = sub.add_parser(
        "uk-corpus",
        help=(
            "UK corpus acquisition into the Farchive "
            "(enumerate, download, affecting, refresh, repair)"
        ),
        description=(
            "Native UK corpus sync.  Resumable, idempotent batch pipeline that "
            "only fetches what is missing or stale: enumerate primary rows, "
            "download enacted/current/effects sources, fetch affecting acts, "
            "refresh mutable sources, and repair cached Multiple Choices "
            "ambiguity pages.  For one-statute debugging, use lawvm uk-acquire; "
            "both surfaces share UK source-state and Multiple Choices rules."
        ),
    )
    uk_corpus_sub = uk_corpus_p.add_subparsers(dest="uk_corpus_command", metavar="<subcommand>")

    def _uk_corpus_db(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--db", default="data/uk_legislation.farchive", metavar="PATH", help="Farchive DB path"
        )

    uk_corpus_acquire_p = uk_corpus_sub.add_parser(
        "acquire", help="enumerate primary acts via CSV and download enacted/current/effects"
    )
    _uk_corpus_db(uk_corpus_acquire_p)
    uk_corpus_acquire_p.add_argument(
        "--types", nargs="+", default=None, metavar="TYPE", help="primary act types (default: ukpga asp asc nia eur)"
    )
    uk_corpus_acquire_p.add_argument(
        "--enacted-only", dest="enacted_only", action="store_true", help="skip current XML + effects feeds"
    )
    uk_corpus_acquire_p.add_argument("--delay", type=float, default=0.3, metavar="SECS")

    uk_corpus_affecting_p = uk_corpus_sub.add_parser(
        "affecting", help="fetch enacted XML for affecting acts found in effects feeds"
    )
    _uk_corpus_db(uk_corpus_affecting_p)
    uk_corpus_affecting_p.add_argument("--affecting-types", nargs="+", default=None, metavar="TYPE")
    uk_corpus_affecting_p.add_argument("--events-jsonl", metavar="PATH", help="write acquisition-event rows")
    uk_corpus_affecting_p.add_argument("--delay", type=float, default=0.3, metavar="SECS")

    uk_corpus_refresh_p = uk_corpus_sub.add_parser(
        "refresh", help="re-fetch mutable resources (current XML + effects feeds) if stale"
    )
    _uk_corpus_db(uk_corpus_refresh_p)
    uk_corpus_refresh_p.add_argument(
        "--statute", action="append", default=[], metavar="STATUTE_ID", help="target one statute (repeatable)"
    )
    uk_corpus_refresh_p.add_argument(
        "--force-refresh", dest="force_refresh", action="store_true", help="refetch even if TTL says fresh"
    )
    uk_corpus_refresh_p.add_argument("--delay", type=float, default=0.3, metavar="SECS")

    uk_corpus_repair_mc_p = uk_corpus_sub.add_parser(
        "repair-multiple-choices",
        help="fetch leaf XML for cached UK Multiple Choices ambiguity pages",
    )
    _uk_corpus_db(uk_corpus_repair_mc_p)
    uk_corpus_repair_mc_p.add_argument(
        "--statute",
        action="append",
        default=[],
        metavar="STATUTE_ID",
        help="target one ambiguous statute id (repeatable)",
    )
    uk_corpus_repair_mc_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="maximum ambiguous locators to repair (0 = no limit)",
    )
    uk_corpus_repair_mc_p.add_argument("--delay", type=float, default=0.3, metavar="SECS")

    uk_corpus_all_p = uk_corpus_sub.add_parser(
        "all",
        help="acquire + affecting + refresh + repair Multiple Choices",
    )
    _uk_corpus_db(uk_corpus_all_p)
    uk_corpus_all_p.add_argument("--types", nargs="+", default=None, metavar="TYPE")
    uk_corpus_all_p.add_argument("--enacted-only", dest="enacted_only", action="store_true")
    uk_corpus_all_p.add_argument("--delay", type=float, default=0.3, metavar="SECS")

    uk_corpus_stats_p = uk_corpus_sub.add_parser("stats", help="archive summary")
    _uk_corpus_db(uk_corpus_stats_p)
    uk_corpus_traindict_p = uk_corpus_sub.add_parser("train-dict", help="train the xml compression dictionary")
    _uk_corpus_db(uk_corpus_traindict_p)
    uk_corpus_repack_p = uk_corpus_sub.add_parser("repack", help="repack xml blobs against the current dictionary")
    _uk_corpus_db(uk_corpus_repack_p)

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
        "--lowering-reason-code",
        metavar="CODE",
        help="only show rows carrying this lowering observation or rejection reason_code",
    )
    uk_effects_p.add_argument(
        "--blocking-only",
        action="store_true",
        help="only show rows carrying at least one blocking lowering rejection",
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
        "--claim-template-status",
        choices=["available", "not_available"],
        help=(
            "only show actionable manual-frontier rows where a suggested claim "
            "template is available or not_available"
        ),
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
        "--fast-limit",
        action="store_true",
        help=(
            "with diagnostic filters and --limit, stop after enough matching "
            "post-summary rows are found instead of counting all matches"
        ),
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

    # --- uk-cross-statute-graph ---
    uk_cross_statute_graph_p = sub.add_parser(
        "uk-cross-statute-graph",
        help="extract the UK cross-statute reference/delegation graph",
        description=(
            "Read-only §23 instrumentation. Extracts typed cross-statute edges "
            "(source provision --relation--> target provision) from one or more "
            "statutes' effects feeds, reusing the UK effect feed and "
            "source-adjudication classifiers. Never replays or mutates ops; emits "
            "a deterministic, canonically-sorted, diffable edge artifact with "
            "node/edge counts by relation, dangling-target detection, and "
            "delegation depth."
        ),
    )
    uk_cross_statute_graph_p.add_argument(
        "statute_id",
        nargs="+",
        help="one or more UK statute IDs, e.g. ukpga/2000/26",
    )
    uk_cross_statute_graph_p.add_argument(
        "--relation",
        metavar="RELATION",
        help=(
            "only show edges of this relation "
            "(amends/repeals/commences/applies_by_reference/confers_power/"
            "modifies/references)"
        ),
    )
    uk_cross_statute_graph_p.add_argument(
        "--applicability-mode",
        dest="uk_applicability_mode",
        choices=UK_APPLICABILITY_MODE_CHOICES,
        default=None,
        help="UK replay applicability lens annotated on the report",
    )
    uk_cross_statute_graph_p.add_argument(
        "--summary-only",
        action="store_true",
        help="print only aggregate node/edge/relation summary statistics",
    )
    uk_cross_statute_graph_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_cross_statute_graph_p.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable cross-statute graph evidence report",
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

    # --- uk-misses ---
    uk_misses_p = sub.add_parser(
        "uk-misses",
        help="full bucketed replay-vs-oracle EID miss worklist for a UK statute",
        description=(
            "Replays a UK statute and prints the complete EID miss sets "
            "(oracle-only and replay-only), bucketed by structural container "
            "so the largest miss clusters surface first.  Includes the "
            "compile-rejection tally for diagnosing what to fix next."
        ),
    )
    uk_misses_p.add_argument(
        "statute_id",
        help="UK statute ID, e.g. ukpga/1998/42",
    )
    uk_misses_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable miss buckets and rejection counts",
    )
    uk_misses_p.add_argument(
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
        "--compact-json",
        action="store_true",
        help=(
            "with --json, omit bulky per-row diagnostic arrays while preserving "
            "counts and bounded candidate samples"
        ),
    )
    uk_candidates_p.add_argument(
        "--summary-count-limit",
        type=int,
        metavar="N",
        help=(
            "with --json, keep only the top N entries in each aggregate summary "
            "count map and report per-map omissions"
        ),
    )
    uk_candidates_p.add_argument(
        "--row-count-limit",
        type=int,
        metavar="N",
        help=(
            "with --json, keep only the top N entries in each emitted row count "
            "map and report per-row omissions"
        ),
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
        "--manual-compile-evidence-status",
        action="append",
        metavar="STATUS",
        help=(
            "with --manual-compile-evidence-jsonl, export rows with this "
            "manual compile frontier status; repeatable (default: "
            "manual_compile_candidate; use actionable for manual_compile_candidate "
            "+ deterministic_frontend_candidate)"
        ),
    )
    uk_candidates_p.add_argument(
        "--claim-template-status",
        choices=("available", "not_available"),
        default="",
        help=(
            "archive-backed mode only: emit statutes with at least one actionable "
            "manual compile row whose suggested claim template status matches"
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
        "--replay-adjudication-evidence-jsonl",
        metavar="PATH",
        help=(
            "write selected saved-run replay adjudications as JSONL review work "
            "items; combines with --replay-adjudication-kind"
        ),
    )
    uk_candidates_p.add_argument(
        "--residual-claim-evidence-jsonl",
        metavar="PATH",
        help=(
            "write selected saved-run replay/oracle residual claims as JSONL "
            "review work items"
        ),
    )
    uk_candidates_p.add_argument(
        "--replay-adjudication-sample-limit",
        type=int,
        default=5,
        metavar="N",
        help="maximum replay adjudication samples to include per emitted statute (default: 5)",
    )

    uk_manual_frontier_validate_p = sub.add_parser(
        "uk-manual-frontier-validate",
        help="validate exported UK manual-frontier JSONL rows against current lowering",
        description=(
            "Re-summarize exported uk-candidates manual-frontier workqueue rows "
            "against the current archive-backed compiler and mark stale/resolved rows."
        ),
    )
    uk_manual_frontier_validate_p.add_argument(
        "input",
        metavar="INPUT",
        help="manual-frontier JSONL path exported by uk-candidates",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path (default: data/uk_legislation.farchive)",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable validation rows",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-row validation details from stdout while preserving summary counts and JSONL exports",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--validation-jsonl",
        metavar="PATH",
        help="write all validation findings as JSONL",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--remaining-jsonl",
        metavar="PATH",
        help=(
            "write original workqueue rows still classified as live manual-frontier "
            "items, annotated with current validation"
        ),
    )
    uk_manual_frontier_validate_p.add_argument(
        "--remaining-manual-rule",
        action="append",
        metavar="RULE_ID",
        help=(
            "when writing --remaining-jsonl, include only live rows whose current "
            "manual-frontier rule matches RULE_ID; may be repeated"
        ),
    )
    uk_manual_frontier_validate_p.add_argument(
        "--remaining-source-pathology",
        action="append",
        metavar="PATHOLOGY",
        help=(
            "when writing --remaining-jsonl, include only live rows whose current "
            "source pathology matches PATHOLOGY; may be repeated"
        ),
    )
    uk_manual_frontier_validate_p.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="exit 1 when any input row is already resolved or no longer live manual-frontier work",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--fail-on-validation-error",
        action="store_true",
        help="exit 1 when any input row is malformed or its effect_id is no longer found",
    )
    uk_manual_frontier_validate_p.add_argument(
        "--fail-on-remaining",
        action="store_true",
        help="exit 1 when any input row is still live manual-frontier work",
    )

    uk_semantic_claims_validate_p = sub.add_parser(
        "uk-semantic-claims-validate",
        help="validate proposed UK semantic-compile claims as non-executable evidence",
        description=(
            "Validate lawvm.uk_semantic_compile_claim.v1 rows for required "
            "schema fields, non-executable operation shape, declared template "
            "proof obligations, and, when supplied, exported manual-frontier "
            "workqueue provenance. Accepted rows do not authorize replay."
        ),
    )
    uk_semantic_claims_validate_p.add_argument(
        "input",
        metavar="INPUT",
        help="semantic-compile claim JSONL path",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--workqueue-jsonl",
        metavar="PATH",
        help="optional manual-frontier JSONL path exported by uk-effects or uk-candidates",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--live-targets-jsonl",
        metavar="PATH",
        help=(
            "optional non-executable live target index JSONL with "
            "lawvm.uk_live_target_index.v1 rows"
        ),
    )
    uk_semantic_claims_validate_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable validation report",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-row validation details from stdout while preserving summary counts and JSONL exports",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--validation-jsonl",
        metavar="PATH",
        help="write all semantic-claim validation findings as JSONL",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--fail-on-rejected",
        action="store_true",
        help="exit 1 after reporting if any claim row is rejected",
    )
    uk_semantic_claims_validate_p.add_argument(
        "--fail-on-input-error",
        action="store_true",
        help="exit 1 after reporting if any input JSONL row is malformed",
    )

    uk_live_target_index_p = sub.add_parser(
        "uk-live-target-index",
        help="export non-executable UK target paths for semantic-claim validation",
        description=(
            "Export lawvm.uk_live_target_index.v1 rows from archived UK current or "
            "enacted XML. The output is validation evidence only and does not "
            "authorize replay."
        ),
    )
    uk_live_target_index_p.add_argument(
        "statute_ids",
        nargs="+",
        metavar="ID",
        help="UK statute id such as ukpga/2000/1",
    )
    uk_live_target_index_p.add_argument(
        "--source",
        choices=("current", "enacted"),
        default="current",
        help="archived XML lane to index (default: current)",
    )
    uk_live_target_index_p.add_argument(
        "--db",
        metavar="PATH",
        help="UK farchive path (default: data/uk_legislation.farchive)",
    )
    uk_live_target_index_p.add_argument(
        "--out",
        metavar="PATH",
        help="write target-index JSONL to PATH",
    )
    uk_live_target_index_p.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON report",
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
    # Inlined from lawvm.tools.frontier.register_cli to avoid importing frontier.py
    # at parser-build time (frontier → lxml/icontract/proof_surfaces ~127 ms standalone).
    # Dispatch in main() still imports frontier lazily; this is pure argparse only.
    frontier_p = sub.add_parser(
        "frontier",
        help="honest frontier report — ranked fixable replay targets",
        description=(
            "Combine bench results with oracle-check classifications to rank "
            "low-scoring statutes by fixability. Separates real replay bugs "
            "(fixable) from oracle-suspect, editorial-convention, and "
            "source-incomplete failures (not fixable)."
        ),
    )
    frontier_p.add_argument(
        "--label",
        metavar="LABEL",
        required=True,
        help="bench run label to analyse, e.g. v_post_merge",
    )
    frontier_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode for fresh oracle-check and score refresh (default: official_consolidation)",
    )
    frontier_p.add_argument(
        "--top",
        type=int,
        default=30,
        help="number of top fixable targets to show (default: 30)",
    )
    frontier_p.add_argument(
        "--exclude-suspect",
        dest="exclude_suspect",
        action="store_true",
        help="omit oracle-suspect statutes from the ranked list",
    )
    frontier_p.add_argument(
        "--bucket",
        choices=[
            "oracle_version_suspect",
            "no_oracle_check",
            "source_pathology",
            "html_noncommensurable",
            "html_topology",
            "contingent_effective_date",
            "base_drift",
            "other_suspect",
            "candidate",
        ],
        help="filter the ranked list to one frontier bucket",
    )
    frontier_p.add_argument(
        "--bucket-report",
        action="store_true",
        help="print a compact top-N-per-bucket report from the current refreshed frontier",
    )
    frontier_p.add_argument(
        "--proof-report",
        action="store_true",
        help="attach live proof-tier summaries for the displayed frontier statutes",
    )
    frontier_p.add_argument(
        "--proof-summary",
        action="store_true",
        help="summarize proof tiers and proof kinds across the current proof-report rows",
    )
    frontier_p.add_argument(
        "--proof-export",
        metavar="PATH",
        help="write proof-report rows as JSONL (default: data/frontier_reports/<label>_frontier_proof.jsonl when --proof-report)",
    )
    frontier_p.add_argument(
        "--evidence-export",
        metavar="PATH",
        help="write full evidence bundles for the displayed frontier rows as JSONL",
    )
    frontier_p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit the refreshed frontier snapshot as JSON",
    )
    frontier_p.add_argument(
        "--strict-label",
        dest="strict_label",
        metavar="LABEL",
        help="strict run label to load projection-row data from (e.g. strict_v1)",
    )
    frontier_p.add_argument(
        "--corpus",
        metavar="CSV_PATH",
        help="optional corpus CSV to restrict the bench run analysis to a subset of statutes",
    )
    frontier_p.add_argument(
        "--export-low-corpus",
        dest="export_low_corpus",
        metavar="CSV_PATH",
        help="write the current low-scoring corpus slice (after score refresh) to CSV",
    )
    frontier_p.add_argument(
        "--db",
        metavar="PATH",
        help="path to divergences.db for pre-computed oracle-check data (default: .tmp/divergences.db if it exists)",
    )
    frontier_p.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        metavar="SCORE",
        help="only consider statutes scoring below this (default: 0.95)",
    )
    frontier_p.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help="workers for fresh oracle-check runs (default: cpu_count)",
    )
    frontier_p.add_argument(
        "--refresh-all-oracle-check",
        dest="refresh_all_oracle_check",
        action="store_true",
        help=(
            "force a live oracle-check refresh for all low-scoring statutes, "
            "even when the candidate pool is too large for the default full-refresh heuristic"
        ),
    )
    frontier_p.add_argument(
        "--refresh-all-scores",
        dest="refresh_all_scores",
        action="store_true",
        help=(
            "force a live score refresh for all low-scoring statutes, "
            "even when the candidate pool is too large for the default score-refresh heuristic"
        ),
    )
    frontier_p.add_argument(
        "--cache-only",
        dest="cache_only",
        action="store_true",
        help=(
            "do not run live oracle-check or score refresh; inspect only saved bench, "
            "divergence DB, strict-run, and cache-only version gates"
        ),
    )
    frontier_p.add_argument(
        "--no-save",
        dest="no_save",
        action="store_true",
        help="do not write CSV to data/frontier_reports/",
    )

    # --- bench-triage ---
    # Inlined from lawvm.tools.bench_triage.register_cli to avoid importing it
    # (→ oracle_check → lxml/replay) at parser-build time. Dispatch in main()
    # imports it lazily; this is pure argparse only.
    bench_triage_p = sub.add_parser(
        "bench-triage",
        help="classify residual bench divergences into A/B/C/needs_human",
        description=(
            "Triage the worst divergent statutes from a bench run into "
            "real_parser_gap (A, worth burndown), oracle_error_or_desync (B), "
            "irreducibly_ambiguous (C), or needs_human. Decision-support: "
            "tells you how much of the residual error is even closeable."
        ),
    )
    bench_triage_p.add_argument(
        "--label",
        metavar="LABEL",
        help="bench run label substring (default: latest *_run_*.csv)",
    )
    bench_triage_p.add_argument(
        "--top",
        type=int,
        default=50,
        help="number of worst divergent statutes to triage (default: 50)",
    )
    bench_triage_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument,
        choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
    )
    bench_triage_p.add_argument(
        "--json",
        metavar="PATH",
        help="write the full triage report as JSON to PATH",
    )
    bench_triage_p.add_argument(
        "--runs-dir",
        dest="runs_dir",
        metavar="DIR",
        help="bench_runs directory (default: <repo>/data/bench_runs)",
    )

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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode for single-statute mode (default: official_consolidation)",
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
    # Inlined from lawvm.tools.sweden.register_cli to avoid importing sweden.py
    # at parser-build time (sweden → lawvm.sweden.grafter/fetch → lxml/tree_ops ~41 ms).
    # Dispatch in main() still imports sweden lazily; this is pure argparse only.
    sweden_p = sub.add_parser(
        "sweden",
        help="Sweden frontend helpers (source records, current-text IR, official PDFs)",
        description=(
            "Helpers for the Sweden frontend: archive official SFS artifacts, "
            "fetch live RK current JSON, inspect SourceRecord metadata from "
            "local RK-style JSON, and parse current-text IR."
        ),
    )
    sweden_sub = sweden_p.add_subparsers(dest="sweden_command", metavar="<subcommand>")

    sw_compile_p = sweden_sub.add_parser(
        "compile-official",
        help="compile first-pass replace ops from archived official act JSON",
    )
    sw_compile_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_compile_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_compile_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_current_p = sweden_sub.add_parser(
        "fetch-current",
        help="fetch RK current JSON and archive it",
    )
    sw_current_p.add_argument("sfs_id", help="SFS ID, e.g. 2025:399")
    sw_current_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_current_p.add_argument(
        "--max-age-hours",
        dest="max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_current_p.add_argument(
        "--show-json",
        action="store_true",
        help="print archived current JSON after fetch",
    )

    sw_fetch_p = sweden_sub.add_parser(
        "fetch-official",
        help="fetch official SFS doc page + PDF, archive raw and extracted text",
    )
    sw_fetch_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_fetch_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_fetch_p.add_argument(
        "--max-age-hours",
        dest="max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override cache max age; default is immutable/no refetch for official sources",
    )
    sw_fetch_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_fetch_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text after fetch",
    )
    sw_fetch_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )

    sw_fetch_p.add_argument(
        "--pdf-url",
        metavar="URL",
        help="explicit direct official PDF URL; used when the doc page is blocked or unavailable",
    )

    sw_hydrate_bulk_p = sweden_sub.add_parser(
        "hydrate-bulk",
        help="bulk hydrate Sweden official/current artifacts into sweden.farchive",
    )
    sw_hydrate_bulk_p.add_argument(
        "sfs_ids",
        nargs="*",
        help="optional explicit SFS IDs; default is all archived official.doc.html locators",
    )
    sw_hydrate_bulk_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_hydrate_bulk_p.add_argument(
        "--scrape-json",
        metavar="PATH",
        help="optional browser-scraped doc-page JSON to ingest before hydrating",
    )
    sw_hydrate_bulk_p.add_argument(
        "--hydrate-current",
        action="store_true",
        help="also fetch RK current JSON and archive source/current bundle artifacts",
    )
    sw_hydrate_bulk_p.add_argument(
        "--compile-ops",
        action="store_true",
        help="compile archived official act JSON into official.ops.json when the act is amending",
    )
    sw_hydrate_bulk_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_hydrate_bulk_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_hydrate_bulk_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_hydrate_bulk_p.add_argument(
        "--no-skip-complete",
        action="store_true",
        help="do not skip SFS IDs that already have the requested archived artifacts",
    )
    sw_hydrate_bulk_p.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="skip the first N input IDs after archive/scrape expansion",
    )
    sw_hydrate_bulk_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="process at most N IDs after offset (default: all)",
    )
    sw_hydrate_bulk_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_backfill_p = sweden_sub.add_parser(
        "backfill-official",
        help="exhaustively probe Sweden SFS IDs and hydrate official artifacts into sweden.farchive",
    )
    sw_backfill_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_backfill_p.add_argument(
        "--year-start",
        type=int,
        default=1999,
        metavar="YEAR",
        help="first SFS year to probe (default: 1999)",
    )
    sw_backfill_p.add_argument(
        "--year-end",
        type=int,
        default=2026,
        metavar="YEAR",
        help="last SFS year to probe (default: 2026)",
    )
    sw_backfill_p.add_argument(
        "--max-number",
        type=int,
        default=2100,
        metavar="N",
        help="maximum SFS number to probe per year (default: 2100)",
    )
    sw_backfill_p.add_argument(
        "--hydrate-current",
        action="store_true",
        help="also fetch RK current JSON and archive source/current bundle artifacts",
    )
    sw_backfill_p.add_argument(
        "--compile-ops",
        action="store_true",
        help="compile archived official act JSON into official.ops.json when the act is amending",
    )
    sw_backfill_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_backfill_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_backfill_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_backfill_p.add_argument(
        "--no-skip-complete",
        action="store_true",
        help="do not skip SFS IDs that already have the requested archived artifacts",
    )
    sw_backfill_p.add_argument(
        "--resume",
        action="store_true",
        help="resume from the archive checkpoint artifact when the run signature matches",
    )
    sw_backfill_p.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="skip the first N candidate IDs after generation",
    )
    sw_backfill_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="process at most N candidate IDs after offset (default: all)",
    )
    sw_backfill_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_hydrate_p = sweden_sub.add_parser(
        "hydrate-live",
        help="fetch RK current JSON and official PDF artifacts, then archive the Sweden bundle",
    )
    sw_hydrate_p.add_argument("sfs_id", help="SFS ID, e.g. 2025:399")
    sw_hydrate_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_hydrate_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_hydrate_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_hydrate_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_hydrate_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text after hydration",
    )
    sw_hydrate_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )
    sw_hydrate_p.add_argument(
        "--pdf-url",
        metavar="URL",
        help="explicit direct official PDF URL; used when the doc page is blocked or unavailable",
    )

    sw_materialize_p = sweden_sub.add_parser(
        "materialize-current",
        help="materialize archived RK current JSON at one date",
    )
    sw_materialize_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:106")
    sw_materialize_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_materialize_p.add_argument("--as-of", required=True, metavar="DATE", help="materialization date YYYY-MM-DD")
    sw_materialize_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_replay_check_p = sweden_sub.add_parser(
        "replay-check",
        help="replay compiled official ops against a temporal Sweden base and compare to current",
    )
    sw_replay_check_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2026:286")
    sw_replay_check_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_replay_check_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="base SFS ID; defaults to the amended act recorded in official.act.json",
    )
    sw_replay_check_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to the compiled op source effective date",
    )
    sw_replay_check_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_ingest_sfst_oracles_p = sweden_sub.add_parser(
        "ingest-sfst-oracles",
        help="seed the current-text oracle for every sfst-backed gain base (idempotent)",
    )
    sw_ingest_sfst_oracles_p.add_argument(
        "--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)"
    )
    sw_ingest_sfst_oracles_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_coverage_scan_p = sweden_sub.add_parser(
        "coverage-scan",
        help="replay-check every amending act whose base has an oracle and aggregate agreement",
    )
    sw_coverage_scan_p.add_argument(
        "--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)"
    )
    sw_coverage_scan_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="scan only the first N covered acts (default: all; sampling is reported)",
    )
    sw_coverage_scan_p.add_argument(
        "--workers",
        type=int,
        default=8,
        metavar="N",
        help="parallel worker processes for the per-act scan (default: 8)",
    )
    sw_coverage_scan_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_diagnose_replay_p = sweden_sub.add_parser(
        "diagnose-replay",
        help="analyze whether one Sweden act can be replayed from the archived current base surface",
    )
    sw_diagnose_replay_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2018:1381")
    sw_diagnose_replay_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_diagnose_replay_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="override the base SFS ID if it cannot be inferred from the official act",
    )
    sw_diagnose_replay_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to the compiled op source effective date",
    )
    sw_diagnose_replay_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_diagnose_replay_p.add_argument(
        "--fetch-missing",
        dest="fetch_missing",
        action="store_true",
        help="when printing older-base diagnostics, try to fetch missing official-chain artifacts",
    )
    sw_diagnose_replay_p.add_argument(
        "--probe-sources",
        dest="probe_sources",
        action="store_true",
        help="probe public official-source reachability for older-base blockers",
    )

    sw_plan_older_base_p = sweden_sub.add_parser(
        "plan-older-base",
        help="plan older-base reconstruction from the base act's official chain inputs",
    )
    sw_plan_older_base_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2018:1381")
    sw_plan_older_base_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_plan_older_base_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="override the base SFS ID if it cannot be inferred from the official act",
    )
    sw_plan_older_base_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to official ops source or the base amendment register",
    )
    sw_plan_older_base_p.add_argument(
        "--fetch-missing",
        dest="fetch_missing",
        action="store_true",
        help="try to fetch missing official-chain artifacts before reporting statuses",
    )
    sw_plan_older_base_p.add_argument(
        "--probe-sources",
        dest="probe_sources",
        action="store_true",
        help="probe public official-source reachability for missing base/chain acts",
    )
    sw_plan_older_base_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_probe_p = sweden_sub.add_parser(
        "probe",
        help="refresh/fetch and replay-check a batch of Sweden acts",
    )
    sw_probe_p.add_argument("sfs_ids", nargs="+", help="amending SFS IDs, e.g. 2026:280 2026:286 2026:290")
    sw_probe_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_probe_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext / official-act parse before probing",
    )
    sw_probe_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_probe_base_p = sweden_sub.add_parser(
        "probe-base",
        help="fetch one base statute, read its amendment register, and probe listed amending acts",
    )
    sw_probe_base_p.add_argument("base_sfs_id", help="base SFS ID, e.g. 2015:284")
    sw_probe_base_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_probe_base_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="probe only the first N register entries (default: all)",
    )
    sw_probe_base_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext / official-act parse before probing",
    )
    sw_probe_base_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_show_official_p = sweden_sub.add_parser(
        "show-official",
        help="inspect the parsed official SFS act surface",
    )
    sw_show_official_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_official_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_official_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_show_official_p.add_argument(
        "--show-text",
        action="store_true",
        help="print enacting clause, provisions, and effective clause",
    )

    sw_show_official_ops_p = sweden_sub.add_parser(
        "show-official-ops",
        help="inspect compiled first-pass ops from archived official act JSON",
    )
    sw_show_official_ops_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_official_ops_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_official_ops_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_source_p = sweden_sub.add_parser(
        "source-record",
        help="build a Sweden SourceRecord from local RK-style JSON",
    )
    sw_source_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_source_p.add_argument(
        "--doc-html",
        metavar="PATH",
        help="optional local official SFS doc page HTML to enrich PDF URL",
    )

    sw_parse_p = sweden_sub.add_parser(
        "parse-current",
        help="parse current-text IR from local RK-style JSON",
    )
    sw_parse_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_parse_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_ingest_p = sweden_sub.add_parser(
        "ingest-json",
        help="archive local RK-style JSON and derived Sweden bundle artifacts",
    )
    sw_ingest_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_ingest_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_ingest_p.add_argument(
        "--doc-html",
        metavar="PATH",
        help="optional local official SFS doc page HTML to archive alongside the bundle",
    )

    sw_ingest_scrape_p = sweden_sub.add_parser(
        "ingest-scrape-json",
        help="archive browser-scraped Sweden doc-page HTML map",
    )
    sw_ingest_scrape_p.add_argument("--json-path", required=True, metavar="PATH", help="local scrape JSON file")
    sw_ingest_scrape_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")

    sw_show_p = sweden_sub.add_parser(
        "show-archive",
        help="inspect archived Sweden bundle and PDF-text artifacts",
    )
    sw_show_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_show_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text if available",
    )
    sw_show_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )

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

    # --- fi-periodic-table ---
    fpt_p = sub.add_parser(
        "fi-periodic-table",
        help="render the Finland abstraction periodic table catalog",
        description=(
            "Render the machine-readable Finland periodic table of abstractions: "
            "phase/structure/identity/time/operative/lexical/provenance/evidence/"
            "instrumentation cells with filled/partial/hole status."
        ),
    )
    fpt_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON summary grouped by axis instead of Markdown",
    )

    # --- fi-timeline-robust-sweep ---
    fts_p = sub.add_parser(
        "fi-timeline-robust-sweep",
        help="sweep corpus for robust-tier timeline invariant hits by amend decile",
    )
    fts_p.add_argument(
        "--corpus",
        default="data/finland/bench_core.csv",
        help="bench corpus CSV (default: data/finland/bench_core.csv)",
    )
    fts_p.add_argument("--limit", type=int, default=0, metavar="N", help="cap statutes scanned from corpus head (0 = all)")
    fts_p.add_argument("--tail", type=int, default=0, metavar="N", help="scan highest-amendment tail instead of head")
    fts_p.add_argument(
        "--mode",
        default="legal_pit",
        choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: legal_pit)",
    )
    fts_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    fts_p.add_argument(
        "--json-out",
        default="",
        metavar="PATH",
        help="write machine-readable JSON report to PATH",
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
    from lawvm.new_zealand.bench_corpus import DEFAULT_SMOKE_SIZE
    from lawvm.new_zealand.chain_replay_corpus import (
        DEFAULT_WORKERS as NZ_CHAIN_REPLAY_CORPUS_DEFAULT_WORKERS,
    )
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
        "--timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="per-request network timeout in seconds (default: 60)",
    )
    nz_sync_p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress default stderr progress reporting",
    )
    nz_sync_p.add_argument(
        "--progress-interval",
        type=int,
        default=25,
        metavar="N",
        help="print one progress line every N acquisition events (default: 25)",
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
        help="compare candidate NZ XML source tree (or actual replay output) against oracle XML",
        description=(
            "Compare a candidate NZ XML source tree against an oracle XML source "
            "tree as candidate-vs-oracle agreement, typing every mismatch row "
            "into a core agreement-residual family. In the default standalone "
            "mode it compares two archived XML blobs (it produces no candidate "
            "replay). With --from-actual-replay it instead consumes ACTUAL "
            "replay output: it runs the fail-closed actual replay for --work-id "
            "and, for every materialized transition, compares the replay's "
            "materialized after-tree against the archived on-or-after oracle, "
            "carrying the actual-replay refusal lane through as typed residuals "
            "so source-honest disagreement stays distinct from a replay bug."
        ),
    )
    nz_agreement_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_agreement_p.add_argument(
        "--from-actual-replay",
        action="store_true",
        help=(
            "consume actual replay output: run the fail-closed actual replay for "
            "--work-id and compare each materialized after-tree to the archived "
            "on-or-after oracle (instead of two hand-picked XML blobs)"
        ),
    )
    nz_agreement_p.add_argument(
        "--work-id",
        default="",
        metavar="ID",
        help="archived work_id (required with --from-actual-replay)",
    )
    nz_agreement_p.add_argument(
        "--families",
        default="all",
        metavar="SPEC",
        help="with --from-actual-replay, promotable families to replay (default: all)",
    )
    nz_agreement_p.add_argument("--candidate-xml-locator", default="", metavar="LOCATOR")
    nz_agreement_p.add_argument("--oracle-xml-locator", default="", metavar="LOCATOR")
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
    nz_frontier_p = nz_corpus_sub.add_parser(
        "frontier",
        help="emit NZ non-executable manual frontier work items",
        description=(
            "Project blocked/review instruction-workqueue rows into explicit, "
            "reviewable frontier work items with source/target/payload "
            "witnesses, probable adjudication options, official guidance "
            "references, an adjudication prompt, and a next action. Frontier "
            "rows are non-executable and emit no replay or agreement claim."
        ),
    )
    nz_frontier_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_frontier_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_frontier_p.add_argument("--limit", type=int, default=40, metavar="N", help="rows to print/include")
    nz_frontier_p.add_argument("--summary-only", action="store_true", help="emit only frontier summary counts")
    nz_frontier_p.add_argument(
        "--frontier-status",
        choices=("blocked", "review"),
        default="",
        help="filter rows by frontier status",
    )
    nz_frontier_p.add_argument("--frontier-family", default="", help="filter rows by frontier family")
    nz_frontier_p.add_argument(
        "--candidate-operation-family",
        default="",
        help="filter rows by candidate operation family",
    )
    nz_frontier_p.add_argument("--json", action="store_true", help="emit frontier work item report JSON")
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
    nz_dry_run_p = nz_corpus_sub.add_parser(
        "dry-run",
        help="dry-run NZ direct repeal candidates against the archived on-or-after XML oracle",
        description=(
            "Apply preflight-approved, exact-target repeal candidates to an "
            "immutable parsed before-version source tree, producing a candidate "
            "after-tree, and compare it to the archived on-or-after XML oracle. "
            "Each operation emits a mutation-boundary proof. This never enables "
            "actual replay and never mutates the archive."
        ),
    )
    nz_dry_run_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_dry_run_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_dry_run_p.add_argument(
        "--scope",
        choices=(
            "complete-set",
            "selected-family-repeal",
            "selected-family-text-replace",
            "selected-family-replace",
            "selected-family-insert",
        ),
        default="complete-set",
        help=(
            "'complete-set' (default) refuses the whole work unless its full candidate set is "
            "ready_for_dry_run_replay; 'selected-family-repeal' dry-runs the ready repeal operations "
            "even when the work's full candidate set is incomplete; 'selected-family-text-replace' "
            "dry-runs the ready single-occurrence text-substitution operations instead; "
            "'selected-family-replace' dry-runs the structural whole-provision replaced/substituted "
            "operations (amend-subtree payload swapped for the target subtree); "
            "'selected-family-insert' dry-runs the structural whole-provision inserted/added operations "
            "(new node from the amend-subtree payload added next to a derived anchor sibling). The selected-"
            "family scopes declare the partial scope and the typed not-in-scope operation-witness counts"
        ),
    )
    nz_dry_run_p.add_argument("--summary-only", action="store_true", help="emit only dry-run summary counts")
    nz_dry_run_p.add_argument("--json", action="store_true", help="emit dry-run report JSON")
    nz_dry_run_oracle_p = nz_corpus_sub.add_parser(
        "dry-run-oracle",
        help="compare the whole dry-run candidate after-tree to the archived on-or-after XML oracle",
        description=(
            "Materialize the full candidate after-document for each dry-run "
            "repeal window (the immutable parsed before tree with the window's "
            "repeal targets tombstoned), compare it node-for-node against the "
            "archived on-or-after XML oracle, and classify every residual. "
            "The repeal slice (mutated targets only) is reported separately from "
            "whole-tree agreement so that source-honest unapplied non-repeal "
            "changes in the window are not mistaken for replay-direction "
            "divergence. This never enables actual replay and never mutates the "
            "archive."
        ),
    )
    nz_dry_run_oracle_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_dry_run_oracle_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_dry_run_oracle_p.add_argument(
        "--summary-only", action="store_true", help="emit only whole-tree comparison summary counts"
    )
    nz_dry_run_oracle_p.add_argument("--json", action="store_true", help="emit comparison report JSON")
    nz_replay_actual_p = nz_corpus_sub.add_parser(
        "replay-actual",
        help="strict actual (canonical) replay of dry-run-verified ops, fail-closed",
        description=(
            "Phase-4 actual replay. Consume ONLY operations the dry-run surface "
            "already verified (a per-op mutation-boundary proof that agrees with "
            "the archived on-or-after oracle AND preserved its neighbours), "
            "materialize ONE transition at a time as (archived before version) + "
            "(authorized ops) -> (candidate after version), and re-confirm the "
            "materialized target slice against the archived on-or-after oracle. "
            "It FAILS CLOSED: a declared transition is materialized only when "
            "EVERY op in its change window is dry-run-verified; any unverified op "
            "blocks the whole transition with a distinct named diagnostic and "
            "nothing is materialized for it (never a silent skip). Only the four "
            "safest families are promotable: direct repeal, direct "
            "single-occurrence text substitution, structural whole-provision "
            "replace, and structural whole-provision or nested insert. The output is a separate "
            "artifact from the official NZ XML, labeled candidate/replay/oracle; "
            "the archived oracle is what the replay is checked against, never the "
            "replay's payload authority. The actually-replayed transition count "
            "is reported separately from the fail-closed-blocked candidate rows. "
            "This is the only NZ surface where replay_claims is True."
        ),
    )
    nz_replay_actual_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_replay_actual_p.add_argument("--work-id", required=True, metavar="ID", help="archived work_id")
    nz_replay_actual_p.add_argument(
        "--families",
        default="all",
        metavar="SPEC",
        help=(
            "promotable families to actually replay: 'all' (default; repeal + "
            "text_replace + replace + insert), or a comma-separated subset (e.g. "
            "'repeal'). Only repeal, text_replace, replace, and insert are "
            "promotable; any other family is rejected."
        ),
    )
    nz_replay_actual_p.add_argument(
        "--summary-only", action="store_true", help="omit per-transition/per-refusal detail from JSON"
    )
    nz_replay_actual_p.add_argument("--json", action="store_true", help="emit actual-replay report JSON")
    nz_replay_chain_p = nz_corpus_sub.add_parser(
        "replay-chain",
        help="experimental amendment-chain replay (all families) on one evolving tree vs the archived oracle",
        description=(
            "First NZ end-to-end replay. Enumerate a base work's authorized "
            "amendment witnesses across all four operation families (repeal, "
            "text_replace, replace, insert; restrict with --families), group them "
            "by effective amendment date into ordered transitions, start from the "
            "EARLIEST archived consolidated version, and apply each transition's "
            "ops to a SINGLE evolving tree carried forward across the whole chain "
            "(unlike the per-window dry-run, which resets to each window's "
            "archived before-tree). At "
            "every archived version date, materialize the evolving tree and "
            "compare it to the archived consolidated oracle with the core "
            "section_similarity metric, producing a similarity CURVE plus typed "
            "skip buckets (every non-applied op is a visible residual, never a "
            "silent drop). This is an experimental dry-run chain replay with "
            "partial coverage: it reports similarity, not pass/fail, never "
            "authorizes actual replay, and never mutates the archive."
        ),
    )
    nz_replay_chain_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_replay_chain_p.add_argument("--work-id", required=True, metavar="ID", help="archived base work_id")
    nz_replay_chain_p.add_argument(
        "--families",
        default="all",
        metavar="SPEC",
        help=(
            "operation families to fold into the chain: 'all' (default; repeal + "
            "text_replace + replace + insert), 'repeal' (repeal-only baseline), or "
            "a comma-separated subset (e.g. 'repeal,text_replace')"
        ),
    )
    nz_replay_chain_p.add_argument(
        "--summary-only", action="store_true", help="omit per-transition/per-skip detail from JSON"
    )
    nz_replay_chain_p.add_argument("--json", action="store_true", help="emit chain-replay report JSON")
    nz_replay_chain_corpus_p = nz_corpus_sub.add_parser(
        "replay-chain-corpus",
        help="run the all-families amendment-chain replay across a work population and report the honest corpus e2e similarity distribution + ranked extraction caps",
        description=(
            "Corpus-wide aggregator for the all-families chain replay. Run the "
            "per-work evolving-tree replay (see replay-chain) across a work "
            "POPULATION (a curated bench-corpus CSV via --corpus, or the benchmark "
            "sampler) in a process pool, and aggregate the honest end-to-end "
            "numbers: the per-work FINAL stable-combined similarity DISTRIBUTION "
            "(count/mean/median/p25/p75 + a histogram) — the corpus e2e number; "
            "per-family applied vs skipped vs oracle-agreement totals; and the "
            "RANKED skip/extraction-cap census (which extraction gap dominates "
            "corpus-wide, to order the next lane). Reports the raw distribution and "
            "does not flatter; every non-applied op is a typed, visible skip. "
            "Measurement only: never authorizes actual replay, never mutates the "
            "archive."
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_replay_chain_corpus_p.add_argument(
        "--work-id",
        action="append",
        default=[],
        metavar="ID",
        help="specific work_id to include; defaults to the --corpus population or the sampler",
    )
    nz_replay_chain_corpus_p.add_argument(
        "--corpus",
        default=None,
        metavar="CSV",
        help=(
            "read the work population from a curated bench-corpus CSV (work_id column), "
            "e.g. data/nz/bench_corpus_smoke.csv; overrides the sampler. An explicit "
            "--work-id list still takes precedence."
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--max-works", type=int, default=None, metavar="N", help="maximum works (no silent truncation; the cap is stated)"
    )
    nz_replay_chain_corpus_p.add_argument(
        "--work-id-prefix",
        default="",
        metavar="PREFIX",
        help=(
            "restrict the archive-wide default population to work_ids starting with PREFIX "
            "(e.g. 'act_public_'); ignored when --work-id or --corpus is given"
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--min-version-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="restrict the default population to works whose latest archived version is from YEAR or later",
    )
    nz_replay_chain_corpus_p.add_argument(
        "--sample-strategy",
        choices=("head", "stride"),
        default="head",
        help=(
            "how to subsample the filtered default population down to --max-works: 'head' keeps the "
            "lexicographic head; 'stride' takes an evenly-spaced deterministic sample"
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--families",
        default="all",
        metavar="SPEC",
        help=(
            "operation families to fold into each chain: 'all' (default), a single family, or a "
            "comma-separated subset (e.g. 'repeal,text_replace')"
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--workers",
        type=int,
        default=NZ_CHAIN_REPLAY_CORPUS_DEFAULT_WORKERS,
        metavar="N",
        help=(
            f"process-pool worker count (default: {NZ_CHAIN_REPLAY_CORPUS_DEFAULT_WORKERS}); "
            "1 runs serially in-process"
        ),
    )
    nz_replay_chain_corpus_p.add_argument(
        "--summary-only", action="store_true", help="emit only the corpus summary (suppress per-work rows)"
    )
    nz_replay_chain_corpus_p.add_argument(
        "--json", action="store_true", help="emit the corpus chain-replay report JSON"
    )
    nz_build_corpus_p = nz_corpus_sub.add_parser(
        "build-corpus",
        help="generate curated NZ bench corpora (large + smoke) of works with >0 amendments",
        description=(
            "Scan the farchive, keep only works carrying at least one amendment "
            "operation witness (>0 amendments), and write two deterministic CSVs "
            "under data/nz/: bench_corpus.csv (every amendment-bearing work) and "
            "bench_corpus_smoke.csv (a small curated dev slice pinning the dry-run "
            "canaries and spanning operation families). No clock, no randomness; "
            "counts are stated, never silently truncated."
        ),
    )
    nz_build_corpus_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_build_corpus_p.add_argument(
        "--out-dir",
        default="data/nz",
        metavar="DIR",
        help="output directory for the curated CSVs (default: data/nz)",
    )
    nz_build_corpus_p.add_argument(
        "--large-out", default=None, metavar="PATH", help="override large corpus path (default: <out-dir>/bench_corpus.csv)"
    )
    nz_build_corpus_p.add_argument(
        "--smoke-out",
        default=None,
        metavar="PATH",
        help="override smoke corpus path (default: <out-dir>/bench_corpus_smoke.csv)",
    )
    nz_build_corpus_p.add_argument(
        "--work-id-prefix",
        default="act_public_",
        metavar="PREFIX",
        help=(
            "scan only work_ids beginning with PREFIX (default: 'act_public_', the amendment-bearing "
            "public-act class); pass an empty string to scan the full archive"
        ),
    )
    nz_build_corpus_p.add_argument(
        "--smoke-size",
        type=int,
        default=DEFAULT_SMOKE_SIZE,
        metavar="N",
        help=f"target smoke-slice size (default: {DEFAULT_SMOKE_SIZE})",
    )
    nz_build_corpus_p.add_argument("--quiet", action="store_true", help="suppress per-batch scan progress")
    nz_dry_run_corpus_p = nz_corpus_sub.add_parser(
        "dry-run-corpus",
        help="run the NZ dry-run repeal surface across a representative work population",
        description=(
            "Select a representative modern act_public population with the "
            "benchmark sampler, run the per-work dry-run repeal surface over it, "
            "and aggregate the corpus oracle agreement rate and the typed "
            "residual/refusal taxonomy. This generalizes the single-canary "
            "dry-run surface; it never enables actual replay and never mutates "
            "the archive."
        ),
    )
    nz_dry_run_corpus_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_dry_run_corpus_p.add_argument(
        "--work-id",
        action="append",
        default=[],
        metavar="ID",
        help="specific work_id to include; defaults to the sampled default population",
    )
    nz_dry_run_corpus_p.add_argument(
        "--corpus",
        default=None,
        metavar="CSV",
        help=(
            "read the work population from a curated bench-corpus CSV (work_id column), "
            "e.g. data/nz/bench_corpus_smoke.csv; overrides the sampler. An explicit "
            "--work-id list still takes precedence."
        ),
    )
    nz_dry_run_corpus_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_dry_run_corpus_p.add_argument(
        "--work-id-prefix",
        default="",
        metavar="PREFIX",
        help=(
            "restrict the archive-wide default population to work_ids starting with PREFIX "
            "(e.g. 'act_public_' for a representative modern slice); ignored when --work-id is given"
        ),
    )
    nz_dry_run_corpus_p.add_argument(
        "--min-version-year",
        type=int,
        default=None,
        metavar="YEAR",
        help=(
            "restrict the default population to works whose latest archived version is from YEAR "
            "or later; works with no parseable version date are dropped (ignored when --work-id is given)"
        ),
    )
    nz_dry_run_corpus_p.add_argument(
        "--sample-strategy",
        choices=("head", "stride"),
        default="head",
        help=(
            "how to subsample the filtered population down to --max-works: 'head' keeps the "
            "lexicographic head; 'stride' takes an evenly-spaced deterministic sample"
        ),
    )
    nz_dry_run_corpus_p.add_argument(
        "--scope",
        choices=(
            "complete-set",
            "selected-family-repeal",
            "selected-family-text-replace",
            "selected-family-replace",
            "selected-family-insert",
        ),
        default="complete-set",
        help=(
            "'complete-set' (default) only dry-runs works whose full candidate set is ready; "
            "'selected-family-repeal' dry-runs the ready repeal operations in every sampled work; "
            "'selected-family-text-replace' dry-runs the ready single-occurrence text-substitution "
            "operations instead; 'selected-family-replace' dry-runs the structural whole-provision "
            "replaced/substituted operations; 'selected-family-insert' dry-runs the structural "
            "whole-provision inserted/added operations. The selected-family scopes report the corpus-wide "
            "family-witness replay-coverage scoreboard"
        ),
    )
    nz_dry_run_corpus_p.add_argument(
        "--summary-only", action="store_true", help="emit only corpus summary counts (suppress per-work rows)"
    )
    nz_dry_run_corpus_p.add_argument("--json", action="store_true", help="emit corpus dry-run report JSON")
    nz_spec_ledger_p = nz_corpus_sub.add_parser(
        "spec-ledger",
        help="materialize the discovered spec of NZ amendment law as a witness-attribution ledger",
        description=(
            "Run the NZ dry-run loop over a corpus across every supported family "
            "(repeal, text_replace, replace, insert), then attribute each per-op "
            "oracle outcome back to the named rule responsible. The result is the "
            "discovered-spec artifact: per rule_id its believed_spec, confidence, "
            "firing count, and corroborated (oracle agrees) vs contradicted (honest "
            "residual) counts with exemplar works. Reuses the jurisdiction-neutral "
            "spec-ledger core read-only; never enables actual replay or mutates the "
            "archive."
        ),
    )
    nz_spec_ledger_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_spec_ledger_p.add_argument(
        "--work-id",
        action="append",
        default=[],
        metavar="ID",
        help="specific work_id to include; defaults to the --corpus population",
    )
    nz_spec_ledger_p.add_argument(
        "--corpus",
        default=None,
        metavar="CSV",
        help=(
            "read the work population from a curated bench-corpus CSV (work_id column), "
            "e.g. data/nz/bench_corpus_smoke.csv. An explicit --work-id list still wins."
        ),
    )
    nz_spec_ledger_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_spec_ledger_p.add_argument(
        "--json", action="store_true", help="emit the discovered-spec ledger JSON to stdout"
    )
    nz_spec_ledger_p.add_argument(
        "--json-out", default="", metavar="PATH", help="also write the ledger JSON to PATH"
    )
    nz_dry_run_north_star_p = nz_corpus_sub.add_parser(
        "dry-run-north-star",
        help="report the stable combined replay-coverage north-star over all supported dry-run families",
        description=(
            "Pin the replay-coverage denominator to ground-truth amendment "
            "operation witnesses (history notes via the operation surface), run "
            "every supported dry-run family (repeal, text_replace, replace, "
            "insert) over a work population, and report the combined coverage "
            "fraction = the true "
            "percentage of NZ amendment operations we can replay-and-oracle-confirm. "
            "Non-executable-by-design operations (brought-into-force/editorial/"
            "expired) are reported separately, and the unsupported executable "
            "families are reported as the explicit remaining frontier. The "
            "denominator does not grow when candidate extraction improves, so this "
            "fraction is comparable across cycles. Measurement only: never enables "
            "actual replay and never mutates the archive."
        ),
    )
    nz_dry_run_north_star_p.add_argument(
        "--db",
        default="data/nz_legislation.farchive",
        metavar="PATH",
        help="Farchive DB path (default: data/nz_legislation.farchive)",
    )
    nz_dry_run_north_star_p.add_argument(
        "--work-id",
        action="append",
        default=[],
        metavar="ID",
        help="specific work_id to include; defaults to the --corpus population",
    )
    nz_dry_run_north_star_p.add_argument(
        "--corpus",
        default=None,
        metavar="CSV",
        help=(
            "read the work population from a curated bench-corpus CSV (work_id column), "
            "e.g. data/nz/bench_corpus_smoke.csv. An explicit --work-id list still wins."
        ),
    )
    nz_dry_run_north_star_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_dry_run_north_star_p.add_argument(
        "--json", action="store_true", help="emit the north-star report JSON"
    )
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
    nz_benchmark_p.add_argument(
        "--corpus",
        default=None,
        metavar="CSV",
        help=(
            "read the work population from a curated bench-corpus CSV (work_id column), "
            "e.g. data/nz/bench_corpus.csv; overrides the sampler. An explicit "
            "--work-id list still takes precedence."
        ),
    )
    nz_benchmark_p.add_argument("--max-works", type=int, default=None, metavar="N", help="maximum works")
    nz_benchmark_p.add_argument(
        "--work-id-prefix",
        default="",
        metavar="PREFIX",
        help=(
            "restrict the archive-wide default population to work_ids starting with PREFIX "
            "(e.g. 'act_public_' for a representative modern slice); ignored when --work-id is given"
        ),
    )
    nz_benchmark_p.add_argument(
        "--min-version-year",
        type=int,
        default=None,
        metavar="YEAR",
        help=(
            "restrict the default population to works whose latest archived version is from YEAR "
            "or later; works with no parseable version date are dropped (ignored when --work-id is given)"
        ),
    )
    nz_benchmark_p.add_argument(
        "--sample-strategy",
        choices=("head", "stride"),
        default="head",
        help=(
            "how to subsample the filtered population down to --max-works: 'head' keeps the "
            "lexicographic head (legacy default); 'stride' takes an evenly-spaced deterministic "
            "sample across the filtered range"
        ),
    )
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
    nz_benchmark_p.add_argument(
        "--include-actual-replay",
        action="store_true",
        help=(
            "run the strict actual-replay surface per work so the benchmark reports "
            "real replay-coverage + oracle-agreement-by-residual-family lanes and can "
            "compute the dry-run/replay/jurisdiction declaration rungs (slower)"
        ),
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
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode for full pipeline (default: official_consolidation)",
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
        "-j",
        "--jurisdiction",
        default="fi",
        choices=["fi", "ee", "uk"],
        help="bench run jurisdiction (default: fi)",
    )
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
    brg_p.add_argument(
        "--score-column",
        default=None,
        help=(
            "explicit numeric score column to guard, e.g. lev_similarity for "
            "Finland text-similarity regressions; default uses the jurisdiction primary lane"
        ),
    )
    brg_p.add_argument(
        "--duration-threshold-s",
        type=float,
        default=1.0,
        help="per-statute duration_s slowdown threshold when duration guard is enabled (default: 1.0)",
    )
    brg_p.add_argument(
        "--max-duration-regressions",
        type=int,
        default=None,
        dest="max_duration_regressions",
        help="enable duration_s regression guard with this max allowed slowed statute count",
    )
    brg_p.add_argument(
        "--rss-threshold-mb",
        type=float,
        default=64.0,
        help=(
            "run-peak process_maxrss memory-growth threshold when RSS guard "
            "is enabled (default: 64.0 MB)"
        ),
    )
    brg_p.add_argument(
        "--max-rss-regressions",
        type=int,
        default=None,
        dest="max_rss_regressions",
        help="enable process_maxrss_kb regression guard with this max allowed run-peak regression count",
    )
    brg_p.add_argument(
        "--phase-threshold-s",
        type=float,
        default=1.0,
        help="per-statute per-phase slowdown threshold when phase guard is enabled (default: 1.0)",
    )
    brg_p.add_argument(
        "--max-phase-regressions",
        type=int,
        default=None,
        dest="max_phase_regressions",
        help="enable phase timing regression guard with this max allowed slowed row/phase cell count",
    )
    brg_p.add_argument(
        "--phase",
        action="append",
        default=[],
        dest="phase_names",
        metavar="NAME",
        help="phase name to guard when phase regression guard is enabled; repeatable, default guards all phases",
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

    # --- acquire-fi-proposals ---
    acquire_he_p = sub.add_parser(
        "acquire-fi-proposals",
        help="ingest Finnish government proposals (HEs) into fi_government_proposal.farchive",
        description=(
            "Ingest Finlex's government-proposal.zip AKN batch dump into "
            "data/fi_government_proposal.farchive (isolated from finlex.farchive). "
            "Default source: $LAWVM_GOVPROP_ZIP or ~/Downloads/government-proposal.zip. "
            "Per-jurisdiction convention: {jurisdiction_code}_{corpus}.farchive."
        ),
    )
    acquire_he_p.add_argument(
        "--source",
        metavar="LOCATION",
        default=None,
        help=(
            "local path or https:// URL to government-proposal.zip "
            "(default: $LAWVM_GOVPROP_ZIP or ~/Downloads/government-proposal.zip)"
        ),
    )
    acquire_he_p.add_argument(
        "--dest",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: data/fi_government_proposal.farchive)",
    )
    acquire_he_p.add_argument(
        "--full",
        action="store_true",
        help="re-ingest everything, overwriting existing farchive entries (default: incremental)",
    )
    acquire_he_p.add_argument(
        "--incremental",
        action="store_true",
        default=True,
        help="only ingest HE locators not already in farchive (default)",
    )
    acquire_he_p.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="parallel zip-extract worker threads (default: 4)",
    )
    acquire_he_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="debug: ingest only first N HE groups",
    )
    acquire_he_p.add_argument(
        "--year-range",
        dest="year_range",
        default=None,
        metavar="Y1:Y2",
        help="debug: only HEs in year range Y1:Y2 inclusive",
    )
    acquire_he_p.add_argument(
        "--stream-mode",
        dest="stream_mode",
        choices=["tempfile", "range"],
        default="tempfile",
        help="HTTPS streaming mode: tempfile (default) or range",
    )
    acquire_he_p.add_argument(
        "--keep-tempfile",
        dest="keep_tempfile",
        action="store_true",
        help="retain streamed zip after ingest (HTTPS mode only)",
    )
    acquire_he_p.add_argument(
        "--include-pdfs",
        dest="include_pdfs",
        action="store_true",
        help=(
            "store main.pdf blobs in the farchive (default: false). LawVM does "
            "not extract PDF text; structured XML + metadata is sufficient for "
            "all current consumers. Default-off saves ~6-12 GB on the full FI "
            "corpus. Pass this flag only if a downstream consumer needs PDF "
            "content (and consider re-acquiring with --full to add them later)."
        ),
    )
    acquire_he_p.add_argument(
        "--strict",
        action="store_true",
        help="abort on first acquisition failure with non-zero exit",
    )
    acquire_he_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-HE progress",
    )
    acquire_he_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="parse and classify without writing to farchive",
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
    ep_p.add_argument(
    "--corpus",
    metavar="PATH",
    help=(
        "path to corpus CSV (rarely needed; default: 'all' = full farchive). "
        "Use a CSV file only when explicitly scoping a projection run to a "
        "curated subset (e.g. for replay-benchmark scoring); for query / "
        "crosslink / structural projections, leave this unset."
    ),
)
    ep_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=".tmp/projections",
        help="output directory for projections (default: .tmp/projections)",
    )
    ep_p.add_argument("--workers", type=int, default=0, help="parallel workers (default: cpu_count, max 8)")
    ep_p.add_argument(
        "--mode",
        default="official_consolidation",
        type=replay_mode_argument, choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
    )
    ep_p.add_argument("--limit", type=int, metavar="N", help="process only first N statutes")
    ep_p.add_argument(
        "--include-refs",
        dest="include_refs",
        action="store_true",
        help="also export fi_refs.parquet (ReferenceMention cross-statute citations)",
    )
    ep_p.add_argument(
        "--include-actors",
        dest="include_actors",
        action="store_true",
        help="also export fi_actors.parquet (ActorMention institutional actor mentions)",
    )
    ep_p.add_argument(
        "--include-pools",
        dest="include_pools",
        action="store_true",
        help="also export fi_pools.parquet (PoolMention budget-line/quantity mentions)",
    )
    ep_p.add_argument(
        "--include-he-corpus",
        dest="include_he_corpus",
        action="store_true",
        help="also export fi_he_corpus/atoms/law_refs/signatures Parquet from fi_government_proposal.farchive",
    )
    ep_p.add_argument(
        "--include-preparatory-refs",
        dest="include_preparatory_refs",
        action="store_true",
        help="also export fi_preparatory_refs.parquet (PreparatoryReference preparation chain citations)",
    )
    ep_p.add_argument(
        "--include-inline-citations",
        dest="include_inline_citations",
        action="store_true",
        help="also export fi_inline_citations.parquet (InlineCitation body-prose citations)",
    )
    ep_p.add_argument(
        "--include-interlinks",
        dest="include_interlinks",
        action="store_true",
        help="also export lawvm_interlinks.parquet (neutral citation/interlink projection)",
    )
    ep_p.add_argument(
        "--include-sections-text",
        dest="include_sections_text",
        action="store_true",
        help=(
            "also export fi_sections_text.parquet (oracle section-text projection; "
            "enables 'lawvm topic' to search enacted-statute text)"
        ),
    )
    ep_p.add_argument(
        "--he-farchive",
        dest="he_farchive",
        default=None,
        metavar="PATH",
        help="fi_government_proposal.farchive path (default: data/fi_government_proposal.farchive)",
    )
    ep_p.add_argument(
        "--he-data-dir",
        dest="he_data_dir",
        default=None,
        metavar="PATH",
        help="HE projection output directory (default: data/fi/v1)",
    )

    # --- fi-proposals ---
    fp_p = sub.add_parser(
        "fi-proposals",
        help="query Finnish government proposals from fi_he_corpus.parquet",
        description=(
            "Query the fi_he_corpus.parquet projection produced by 'lawvm sync-fi-proposals'. "
            "Without filters, shows the schema and row count."
        ),
        parents=_P,
    )
    fp_p.add_argument(
        "--ministry",
        metavar="TEXT",
        help="filter by ministry name or canonical_id (substring match)",
    )
    fp_p.add_argument(
        "--year",
        type=int,
        metavar="YEAR",
        help="filter to HEs from this year",
    )
    fp_p.add_argument(
        "--year-range",
        dest="year_range",
        metavar="Y1:Y2",
        help="filter to HEs in year range Y1:Y2 inclusive",
    )
    fp_p.add_argument(
        "--lifecycle",
        metavar="STATE",
        help="filter by finlex_state (e.g. 'closed', 'open')",
    )
    fp_p.add_argument(
        "--structured-only",
        dest="structured_only",
        action="store_true",
        help="only show FULL_AKN HEs (is_structured=True)",
    )
    fp_p.add_argument(
        "--pdf-only",
        dest="pdf_only",
        action="store_true",
        help="only show PDF_WRAPPER HEs (is_structured=False)",
    )
    fp_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    fp_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_he_corpus.parquet (default: data/fi/v1)",
    )
    fp_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )

    # --- fi-proposal-show ---
    fps_p = sub.add_parser(
        "fi-proposal-show",
        help="show per-HE structural overview from fi_he_* projections",
        description=(
            "Show metadata + optionally atoms, law_refs, and signatures for one HE. "
            "Default output: metadata only. Use --include-atoms etc. to add body atoms."
        ),
        parents=_P,
    )
    fps_p.add_argument(
        "he_id",
        help="HE identifier, e.g. 'HE 98/1996 vp'",
    )
    fps_p.add_argument(
        "--include-atoms",
        dest="include_atoms",
        action="store_true",
        help="include fi_he_atoms rows (body structure)",
    )
    fps_p.add_argument(
        "--include-law-refs",
        dest="include_law_refs",
        action="store_true",
        help="include fi_he_law_refs rows (citations to enacted statutes)",
    )
    fps_p.add_argument(
        "--include-signatures",
        dest="include_signatures",
        action="store_true",
        help="include fi_he_signatures rows (President/minister signatures)",
    )
    fps_p.add_argument("--limit", type=int, metavar="N", help="limit rows for large tables")
    fps_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_he_*.parquet (default: data/fi/v1)",
    )
    fps_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl"],
        help="output format (default: table)",
    )

    # --- fi-proposal-bundle ---
    fpb_p = sub.add_parser(
        "fi-proposal-bundle",
        help="typed JSON bundle aggregating #1-#5 projections for one HE",
        description=(
            "Compose already-projected Parquet tables (features #1-#5) into a single "
            "typed JSON bundle for one Finnish government proposal.  No new extraction. "
            "Use --all for a complete bundle or select individual --include-* flags. "
            "PDF_WRAPPER HEs return metadata-only bundles with warnings."
        ),
    )
    fpb_p.add_argument(
        "--he",
        dest="he_id",
        default=None,
        metavar="HE_ID",
        help=(
            "HE identifier, e.g. 'HE 98/1996 vp', 'HE/2024/184', or 'HE-184/2024'. "
            "CLI normalises all forms to corpus he_id."
        ),
    )
    fpb_p.add_argument(
        "--branch",
        dest="branch_id",
        default=None,
        metavar="BRANCH_ID",
        help="alternative: branch context ID (if BranchContext uses different IDs)",
    )
    fpb_p.add_argument(
        "--include-atoms",
        dest="include_atoms",
        action="store_true",
        help="include fi_he_atoms rows (body structure atoms, FULL_AKN only)",
    )
    fpb_p.add_argument(
        "--include-law-refs",
        dest="include_law_refs",
        action="store_true",
        help="include fi_he_law_refs rows (typed citations to enacted statutes)",
    )
    fpb_p.add_argument(
        "--include-actors",
        dest="include_actors",
        action="store_true",
        help=(
            "include actor mentions from fi_actors.parquet for statutes referenced "
            "by this HE (requires --include-law-refs data to resolve target statutes)"
        ),
    )
    fpb_p.add_argument(
        "--include-pools",
        dest="include_pools",
        action="store_true",
        help=(
            "include pool/quantity mentions from fi_pools.parquet for statutes "
            "referenced by this HE"
        ),
    )
    fpb_p.add_argument(
        "--include-telos",
        dest="include_telos",
        action="store_true",
        help=(
            "include telos/purpose section text from sections.parquet for parent "
            "statutes referenced by this HE (requires feature #5 telos flag applied)"
        ),
    )
    fpb_p.add_argument(
        "--include-replay-status",
        dest="include_replay_status",
        action="store_true",
        help=(
            "include replay-vs-oracle classification for parent statutes from "
            "statutes.parquet (clean/partial/diverged/unknown)"
        ),
    )
    fpb_p.add_argument(
        "--include-text",
        dest="include_text",
        default="none",
        choices=["none", "affected", "before-after"],
        help=(
            "text rehydration mode for affected provisions (default: none). "
            "'affected' and 'before-after' not yet implemented in projection-based bundle."
        ),
    )
    fpb_p.add_argument(
        "--include-signatures",
        dest="include_signatures",
        action="store_true",
        help="include fi_he_signatures rows (President/minister signatures, FULL_AKN only)",
    )
    fpb_p.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="shorthand for all --include-* flags",
    )
    fpb_p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="limit rows for large per-section tables (atoms, refs, actors, pools)",
    )
    fpb_p.add_argument(
        "--he-data-dir",
        dest="he_data_dir",
        default="data/fi/v1",
        metavar="PATH",
        help="directory containing fi_he_*.parquet (default: data/fi/v1)",
    )
    fpb_p.add_argument(
        "--projections-data-dir",
        dest="projections_data_dir",
        default="data/fi/v1",
        metavar="PATH",
        help=(
            "directory containing fi_actors/fi_pools/sections/statutes.parquet "
            "(default: data/fi/v1)"
        ),
    )
    fpb_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="json",
        choices=["json", "jsonl", "table"],
        help="output format (default: json)",
    )
    fpb_p.add_argument(
        "-j",
        "--jurisdiction",
        dest="jurisdiction",
        default="fi",
        help="jurisdiction (currently only 'fi' supported; default: fi)",
    )

    # --- fi-proposal-history ---
    fph_p = sub.add_parser(
        "fi-proposal-history",
        help="show all HEs that touched a statute (legislative amendment history)",
        description=(
            "Query fi_he_law_refs + fi_he_corpus to list all government proposals "
            "that touched a given statute.  The most common first lausunto question: "
            "'What HEs have amended statute X?' "
            "Requires fi_he_corpus.parquet and fi_he_law_refs.parquet in --data-dir."
        ),
        parents=_P,
    )
    fph_p.add_argument(
        "--statute",
        required=True,
        metavar="STATUTE_ID",
        help="statute to query, e.g. '2014/527'",
    )
    fph_p.add_argument(
        "--lifecycle",
        metavar="STATE",
        choices=["all", "pending", "closed", "enacted", "rejected"],
        default="all",
        help="filter by finlex_state: all|pending|closed|enacted|rejected (default: all)",
    )
    fph_p.add_argument(
        "--year-range",
        dest="year_range",
        metavar="Y1:Y2",
        help="narrow to HEs in year range Y1:Y2 inclusive",
    )
    fph_p.add_argument(
        "--ministry",
        metavar="TEXT",
        help="filter by ministry name or canonical_id (substring match)",
    )
    fph_p.add_argument(
        "--include-provisions",
        dest="include_provisions",
        action="store_true",
        help="show which specific provisions of the statute each HE touched",
    )
    fph_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    fph_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        metavar="PATH",
        help="directory containing fi_he_corpus.parquet and fi_he_law_refs.parquet (default: data/fi/v1)",
    )
    fph_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv"],
        help="output format (default: table)",
    )

    # --- fi-proposals-competing ---
    fpc_p = sub.add_parser(
        "fi-proposals-competing",
        help="show pending HEs that simultaneously amend the same statute (collision detection)",
        description=(
            "Detect concurrent pending government proposals that amend the same statute, "
            "which may cause conflicting section renumbering or overlapping provision edits. "
            "Requires fi_he_corpus.parquet and fi_he_law_refs.parquet in --data-dir."
        ),
        parents=_P,
    )
    fpc_p.add_argument(
        "--statute",
        required=True,
        metavar="STATUTE_ID",
        help="statute to check for concurrent amendments, e.g. '1995/1558'",
    )
    fpc_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        help="check competing proposals as of DATE (YYYY-MM-DD; default: today)",
    )
    fpc_p.add_argument(
        "--lifecycle-window",
        dest="lifecycle_window",
        metavar="WINDOW",
        choices=["pending", "active-this-year", "all"],
        default="pending",
        help="lifecycle window: pending|active-this-year|all (default: pending)",
    )
    fpc_p.add_argument(
        "--provision-overlap",
        dest="provision_overlap",
        action="store_true",
        help="show specific provision overlaps between competing HEs",
    )
    fpc_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    fpc_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        metavar="PATH",
        help="directory containing fi_he_corpus.parquet and fi_he_law_refs.parquet (default: data/fi/v1)",
    )
    fpc_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv"],
        help="output format (default: table)",
    )

    # --- sync-fi-proposals ---
    sfp_p = sub.add_parser(
        "sync-fi-proposals",
        help="acquire Finnish government proposals and rebuild Parquet projections",
        description=(
            "Two-step composition: (1) acquire-fi-proposals updates the farchive, "
            "(2) rebuilds fi_he_corpus/atoms/law_refs/signatures under data/fi/v1/. "
            "Both steps are separately invokable; this provides one ergonomic entrypoint."
        ),
    )
    sfp_p.add_argument(
        "--source",
        metavar="LOCATION",
        default=None,
        help="local path or https:// URL to government-proposal.zip",
    )
    sfp_p.add_argument(
        "--farchive",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: data/fi_government_proposal.farchive)",
    )
    sfp_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        metavar="PATH",
        help="projection output directory (default: data/fi/v1)",
    )
    sfp_p.add_argument(
        "--lang",
        default="fin",
        choices=["fin", "swe"],
        help="language to project (default: fin)",
    )
    sfp_p.add_argument(
        "--full",
        action="store_true",
        help="re-ingest all HEs (default: incremental)",
    )
    sfp_p.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="parallel zip-extract workers (default: 4)",
    )
    sfp_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="debug: process only first N HE groups",
    )
    sfp_p.add_argument(
        "--year-range",
        dest="year_range",
        default=None,
        metavar="Y1:Y2",
        help="debug: only HEs in year range Y1:Y2 inclusive",
    )
    sfp_p.add_argument(
        "--strict",
        action="store_true",
        help="abort on first acquisition failure",
    )
    sfp_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-HE progress",
    )
    sfp_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="parse and classify without writing farchive or projections",
    )
    sfp_p.add_argument(
        "--projection-only",
        dest="projection_only",
        action="store_true",
        help="skip acquisition; only rebuild projections from existing farchive",
    )
    sfp_p.add_argument(
        "--no-parquet",
        dest="no_parquet",
        action="store_true",
        help="write JSONL only (no Parquet)",
    )

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

    # --- refs ---
    refs_p = sub.add_parser(
        "refs",
        help="query ReferenceMention cross-statute citations from fi_refs.parquet",
        description=(
            "Query the fi_refs.parquet projection produced by 'lawvm export-projections'. "
            "Without filters, shows schema and row count. "
            "With --from / --to / --confidence filters, returns matching citation edges."
        ),
        parents=_P,
    )
    refs_p.add_argument(
        "--from",
        dest="from_ref",
        metavar="STATUTE_OR_REF",
        help="citations FROM this statute or provision (e.g. '711/2022' or '711/2022/7')",
    )
    refs_p.add_argument(
        "--to",
        dest="to_ref",
        metavar="STATUTE_OR_REF",
        help="citations TO this statute or provision (e.g. '711/2022' or '711/2022/7')",
    )
    refs_p.add_argument(
        "--cite-kind",
        dest="cite_kind",
        choices=["internal", "cross-statute", "eu", "treaty", "non-statutory-instrument"],
        help="filter by citation kind",
    )
    refs_p.add_argument(
        "--confidence",
        choices=["exact", "approximate", "ambiguous", "unresolved", "broken"],
        help="filter by confidence level",
    )
    refs_p.add_argument(
        "--broken-after",
        dest="broken_after",
        metavar="DATE",
        help="citations that became BROKEN after DATE (YYYY-MM-DD)",
    )
    refs_p.add_argument(
        "--broken-before",
        dest="broken_before",
        metavar="DATE",
        help="citations that became BROKEN before DATE (YYYY-MM-DD)",
    )
    refs_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        help="show references valid at DATE (YYYY-MM-DD)",
    )
    refs_p.add_argument(
        "--include-source-span",
        dest="include_source_span",
        action="store_true",
        help="include source_span_file / source_span_byte_offset / source_span_len columns",
    )
    refs_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    refs_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_refs.parquet (default: data/fi/v1)",
    )
    refs_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    # Note: -j/--jurisdiction is inherited from _P parent parser

    # --- actors ---
    actors_p = sub.add_parser(
        "actors",
        help="query ActorMention institutional actor mentions from fi_actors.parquet",
        description=(
            "Query the fi_actors.parquet projection produced by 'lawvm export-projections'. "
            "Without filters, shows schema and row count. "
            "With filters, returns matching actor mentions."
        ),
        parents=_P,
    )
    actors_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        help="filter to actors FROM this statute (e.g. '711/2022')",
    )
    actors_p.add_argument(
        "--provision",
        metavar="PROVISION_REF",
        help="filter to actors in this provision (e.g. '711/2022/7')",
    )
    actors_p.add_argument(
        "--modal-kind",
        dest="modal_kind",
        metavar="KIND",
        choices=["duty", "discretion", "permission", "prohibition",
                 "mention", "passive-obligation", "passive_obligation", "unresolved"],
        help="filter by modal kind: duty|discretion|permission|prohibition|mention|passive-obligation|unresolved",
    )
    actors_p.add_argument(
        "--confidence",
        metavar="CONF",
        choices=["exact", "registry_resolved", "registry-resolved",
                 "lifecycle_resolved", "lifecycle-resolved", "unresolved"],
        help="filter by resolution confidence: exact|registry-resolved|lifecycle-resolved|unresolved",
    )
    actors_p.add_argument(
        "--role-pattern",
        dest="role_pattern",
        metavar="PATTERN",
        help="SQL SIMILAR TO pattern on actor_canonical_id / actor_canonical_show_as",
    )
    actors_p.add_argument("--as-of", metavar="DATE", help="filter to mentions valid at DATE")
    actors_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    actors_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_actors.parquet (default: data/fi/v1)",
    )
    actors_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    # Note: -j/--jurisdiction is inherited from _P parent parser

    # --- pools ---
    pools_p = sub.add_parser(
        "pools",
        help="query PoolMention budget-line/quantity mentions from fi_pools.parquet",
        description=(
            "Query the fi_pools.parquet projection produced by 'lawvm export-projections'. "
            "Without filters, shows schema and row count. "
            "With filters, returns matching pool/quantity mentions."
        ),
        parents=_P,
    )
    pools_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        help="filter to pools FROM this statute (e.g. '711/2022')",
    )
    pools_p.add_argument(
        "--provision",
        metavar="PROVISION_REF",
        help="filter to pools in this provision (e.g. '711/2022/3')",
    )
    pools_p.add_argument(
        "--quantity-kind",
        dest="quantity_kind",
        metavar="KIND",
        choices=["budget_line", "budget-line", "fiscal_pool", "fiscal-pool",
                 "capacity_cap", "capacity-cap", "threshold",
                 "formula_term", "formula-term", "unresolved"],
        help="filter by quantity kind: budget-line|fiscal-pool|capacity-cap|threshold|formula-term|unresolved",
    )
    pools_p.add_argument(
        "--confidence",
        metavar="CONF",
        choices=["exact", "approximate", "unresolved"],
        help="filter by resolution confidence: exact|approximate|unresolved",
    )
    pools_p.add_argument(
        "--unit",
        metavar="UNIT",
        help="filter by unit string (e.g. 'g Cd/ha/5 v', 'EUR')",
    )
    pools_p.add_argument("--as-of", metavar="DATE", help="filter to mentions valid at DATE")
    pools_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    pools_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_pools.parquet (default: data/fi/v1)",
    )
    pools_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    # Note: -j/--jurisdiction is inherited from _P parent parser

    # --- preparatory-refs ---
    prep_refs_p = sub.add_parser(
        "preparatory-refs",
        help="query PreparatoryReference preparation chain citations from fi_preparatory_refs.parquet",
        description=(
            "Query the fi_preparatory_refs.parquet projection produced by "
            "'lawvm export-projections --include-preparatory-refs' or 'lawvm rebuild-indexes'. "
            "Without filters, shows the schema and row count."
        ),
        parents=_P,
    )
    prep_refs_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        help="filter to refs FROM this statute (e.g. '711/2022')",
    )
    prep_refs_p.add_argument(
        "--kind",
        metavar="KIND",
        help=(
            "filter by kind: he|committee_report|committee_opinion|parliament_response|"
            "parliament_response_comm|law_initiative|eu_regulation|eu_directive|"
            "eu_decision|oj_reference|unresolved"
        ),
    )
    prep_refs_p.add_argument(
        "--committee",
        metavar="ABBREV",
        help="filter by committee abbreviation (e.g. 'HaVM')",
    )
    prep_refs_p.add_argument(
        "--he-year",
        dest="he_year",
        type=int,
        metavar="YEAR",
        help="filter to HE refs from this year",
    )
    prep_refs_p.add_argument(
        "--he-number",
        dest="he_number",
        type=int,
        metavar="N",
        help="filter to HE refs with this sequential number",
    )
    prep_refs_p.add_argument(
        "--eu-celex",
        dest="eu_celex",
        metavar="CELEX",
        help="filter to EU acts with this CELEX identifier (e.g. '32017R2226')",
    )
    prep_refs_p.add_argument("--as-of", metavar="DATE", help="filter to refs valid at DATE")
    prep_refs_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    prep_refs_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_preparatory_refs.parquet (default: data/fi/v1)",
    )
    prep_refs_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    # Note: -j/--jurisdiction is inherited from _P parent parser

    # --- inline-citations ---
    ic_p = sub.add_parser(
        "inline-citations",
        help="query InlineCitation body-prose citations from fi_inline_citations.parquet",
        description=(
            "Query the fi_inline_citations.parquet projection produced by "
            "'lawvm export-projections --include-inline-citations' or 'lawvm rebuild-indexes'. "
            "Without filters, shows the schema and row count."
        ),
        parents=_P,
    )
    ic_p.add_argument(
        "--source-doc-id",
        dest="source_doc_id",
        metavar="ID",
        help="filter to citations FROM this document (e.g. '711/2022' or '116/2024')",
    )
    ic_p.add_argument(
        "--source-doc-kind",
        dest="source_doc_kind",
        metavar="KIND",
        choices=["statute", "he"],
        help="filter by document kind: statute | he",
    )
    ic_p.add_argument(
        "--kind",
        metavar="KIND",
        help=(
            "filter by citation kind: court_kko|court_kho|ombudsman_eoa|chancellor_oka|"
            "statute_inline|he_inline|vtv_report|working_group_memo|parliament_kirjelma|"
            "old_committee|unresolved"
        ),
    )
    ic_p.add_argument(
        "--context",
        metavar="CONTEXT",
        help=(
            "filter by structural context: enacted_statute_body|he_rationale|"
            "he_introduction|preliminary_work|other"
        ),
    )
    ic_p.add_argument(
        "--case-year",
        dest="case_year",
        type=int,
        metavar="YEAR",
        help="filter to citations with this year component (court/eoa/oka/vtv/he/ek)",
    )
    ic_p.add_argument("--as-of", metavar="DATE", help="(reserved; inline citations have no temporal interval)")
    ic_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    ic_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        help="directory containing fi_inline_citations.parquet (default: data/fi/v1)",
    )
    ic_p.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    # Note: -j/--jurisdiction is inherited from _P parent parser

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

    # --- fi-parse-explain ---
    fi_parse_explain_p = sub.add_parser(
        "fi-parse-explain",
        help="dump everything needed to diagnose one statute's johtolause parse",
        description=(
            "Fetch a statute's enacting clause (johtolause) from the corpus and "
            "dump everything needed to diagnose how that ONE clause parses: the "
            "normalized text, the parser_lane + grammar_decline_reason, the "
            "OLD-vs-NEW surface-model comparison, and the no-silent-drop totality "
            "predicate (n_ops + flagged drops). Read-only, deterministic; composes "
            "the existing parser APIs (no re-implementation)."
        ),
    )
    fi_parse_explain_p.add_argument(
        "sid",
        metavar="STATUTE_ID",
        help="statute id, e.g. 2002/375",
    )
    fi_parse_explain_p.add_argument(
        "--ops",
        action="store_true",
        help="also dump the parsed op codes",
    )
    fi_parse_explain_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON",
    )

    # --- fi-parse ---
    fi_parse_p = sub.add_parser(
        "fi-parse",
        help="visualize Finnish parse structures (forest / johtolause / morph / clauses)",
        description=(
            "Render Finnish parse structures from the existing machinery (read-only "
            "visualization; no new parsing). Pick exactly one view: "
            "--statute (forest; narrow with --grep/--provision, or add --clauses for "
            "segmentation), --johtolause TEXT, --morph WORD, or --text TEXT (clauses). "
            "Every view supports --json."
        ),
    )
    fi_parse_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        default="",
        help="statute id for the FOREST / CLAUSES view, e.g. 2004/301",
    )
    fi_parse_p.add_argument(
        "--grep",
        metavar="TEXT",
        default=None,
        help="forest view: narrow to the provision window around this literal text",
    )
    fi_parse_p.add_argument(
        "--provision",
        metavar="ADDR",
        default=None,
        help="forest view: narrow to the provision matching this eId/address",
    )
    fi_parse_p.add_argument(
        "--clauses",
        action="store_true",
        help="with --statute: show sentence/clause segmentation instead of the forest",
    )
    fi_parse_p.add_argument(
        "--johtolause",
        metavar="TEXT",
        default=None,
        help="JOHTOLAUSE view: parse this amendment enacting clause text",
    )
    fi_parse_p.add_argument(
        "--morph",
        metavar="WORD",
        default=None,
        help="MORPH view: generate the case paradigm + reverse-analyze this word",
    )
    fi_parse_p.add_argument(
        "--text",
        metavar="TEXT",
        default=None,
        help="CLAUSES view over raw text (no corpus needed)",
    )
    fi_parse_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )

    # --- analyze-bill ---
    analyze_bill_p = sub.add_parser(
        "analyze-bill",
        help="structured BILL IMPACT REPORT for one amending statute",
        description=(
            "Produce a structured bill-impact report for one amending statute "
            "(read-only; composes the existing johtolause + Legal Surface Graph "
            "machinery, no new parsing). Reports WHAT the bill does (lowered "
            "ops), the surface delta (new delegations / references / definitions "
            "/ broken-reference risk), and a clearly-labelled judgment-frontier "
            "layer of unowned-channel CANDIDATES (not findings). Supports --json."
        ),
    )
    analyze_bill_p.add_argument(
        "statute_id",
        metavar="STATUTE_ID",
        help="amending statute id, e.g. 2018/1138",
    )
    analyze_bill_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )

    # --- bill-counterfactual ---
    bill_cf_p = sub.add_parser(
        "bill-counterfactual",
        help="three-tier counterfactual 'what does this amendment do' report",
        description=(
            "Report one Finnish amendment's effects in THREE structurally distinct "
            "tiers, kept separate and never conflated (read-only projection): "
            "TIER 1 directly-changed provisions (johtolause ops); TIER 2 provisions "
            "in the amended act that CITE a tier-1-changed provision (1-hop internal "
            "back-references, traced only through resolved/unchanged citations); and "
            "TIER 3 a DECLARED boundary of uncomputed second-order effects (the "
            "honesty boundary IS part of the result). No score, no magnitude. "
            "Supports --json."
        ),
    )
    bill_cf_p.add_argument(
        "statute_id",
        metavar="STATUTE_ID",
        help="amending statute id, e.g. 2018/301",
    )
    bill_cf_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )

    # --- fi-refs ---
    fi_refs_p = sub.add_parser(
        "fi-refs",
        help="annotated-source-canvas viewer for the references overlay",
        description=(
            "Render a Finnish statute's references as annotations over its source "
            "text (read-only; no new parsing). Levels (cheapest→richest): "
            "counts / digest / context (default) / full. --only filters to residue "
            "statuses (the audit instrument). --json emits the machine dict."
        ),
    )
    fi_refs_p.add_argument(
        "statute",
        metavar="STATUTE_ID",
        help="statute id, e.g. 2009/953",
    )
    fi_refs_p.add_argument(
        "--level",
        choices=("counts", "digest", "context", "full"),
        default="context",
        help="graduated disclosure level (default: context)",
    )
    fi_refs_p.add_argument(
        "-C",
        "--context",
        type=int,
        default=1,
        metavar="N",
        help="context radius in CLAUSES for the context level (default: 1)",
    )
    fi_refs_p.add_argument(
        "--merge-gap",
        type=int,
        default=0,
        metavar="N",
        help="char gap under which adjacent context windows merge (default: 0)",
    )
    fi_refs_p.add_argument(
        "--split",
        action="store_true",
        help="context level: one window per ref (disable window merge)",
    )
    fi_refs_p.add_argument(
        "--only",
        metavar="STATUSES",
        default=None,
        help=(
            "filter marks to these comma-separated resolution statuses "
            "(e.g. ambiguous,open,broken,unresolved) — the audit spotlight"
        ),
    )
    fi_refs_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        default=None,
        help="bitemporal filter: drop refs whose valid interval excludes this date",
    )
    fi_refs_p.add_argument(
        "--provision",
        metavar="ADDR",
        default=None,
        help="narrow to the provision matching this eId/address",
    )
    fi_refs_p.add_argument(
        "--grep",
        metavar="TEXT",
        default=None,
        help="narrow to the window around this literal text",
    )
    fi_refs_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )

    # --- parse-bench ---
    parse_bench_p = sub.add_parser(
        "parse-bench",
        help="corpus-wide coverage benchmark (fi/ee=grammar coverage; us/nz/uk=lowering coverage)",
        description=(
            "Parse-only coverage benchmark (the parse counterpart to `bench`); "
            "dispatches on the global -j/--jurisdiction flag. Two DISTINCT metrics. "
            "fi/ee = GRAMMAR coverage (free-text amendment grammars): "
            "fi iterates the FULL statute corpus (~59k, no replay/oracle needed) "
            "and reports the fraction of amendment johtolauses the parser consumes "
            "with no interior/trailing silent drop (token-witness coverage); "
            "ee reports the fraction of verb-bearing op-items with no silently-dropped "
            "target LABEL (label coverage). "
            "us/nz/uk = LOWERING coverage (structured amendment data — NOT grammar "
            "coverage): the fraction of pre-typed amendment instructions/effects "
            "already in the corpus that LOWERED into produced ops, plus a ranked "
            "worklist of unhandled/rejected instruction shapes (read-only reuse of "
            "each frontend's existing instruments, replay-free; uk defaults to a "
            "bounded/sampled run honoring --limit). "
            "no/se have neither metric yet and print a pointer to their own report."
        ),
    )
    parse_bench_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap the corpus to the first N statutes (default: no cap = full corpus)",
    )
    parse_bench_p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="worker process count (default: 8)",
    )
    parse_bench_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="show the top N uncovered-span shapes and worst statutes (default: 20)",
    )
    parse_bench_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with coverage, tier counts, top shapes, and dropped statutes",
    )

    # --- refs-bench ---
    refs_bench_p = sub.add_parser(
        "refs-bench",
        help="corpus-wide reference-resolution coverage benchmark (fi)",
        description=(
            "Parse-only, replay-free coverage benchmark for the reference/interlink "
            "layer (the reference-layer counterpart to `parse-bench`). Dispatches on "
            "the global -j/--jurisdiction flag; only fi has a free-text reference "
            "recognizer today. Reads each statute's cached body XML and runs "
            "`extract_all_reference_mentions` READ-ONLY (no oracle replay, no apply, "
            "no diff), then tallies the resolution-status distribution (each "
            "CiteConfidence), the per-CiteKind breakdown, and a ranked inventory of "
            "the residue shapes (sub-EXACT mentions + rejected candidates) — the "
            "reference worklist. Headline metric: corpus-wide fraction of mentions "
            "that are EXACT vs the residue tail. Use --limit N to sample cheaply."
        ),
    )
    refs_bench_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap the corpus to the first N statutes (default: no cap = full corpus)",
    )
    refs_bench_p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="worker process count (default: min(8, cpu-2) to respect memory ceiling)",
    )
    refs_bench_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="show the top N residue shapes and worst statutes (default: 20)",
    )
    refs_bench_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with coverage, status/kind counts, top residue shapes",
    )
    refs_bench_p.add_argument(
        "--mode",
        choices=("precision", "recall", "both"),
        default="precision",
        help=(
            "precision = resolution-status of EMITTED mentions (default, original "
            "behavior); recall = anchor-driven coverage proxy (which reference-bearing "
            "anchors the recognizers MISS); both = run precision then recall"
        ),
    )
    refs_bench_p.add_argument(
        "--recall",
        action="store_true",
        help="shorthand for --mode recall (anchor-driven recall/miss-worklist pass)",
    )
    refs_bench_p.add_argument(
        "--scorecard",
        action="store_true",
        help=(
            "print the per-family SCORECARD: for each cite-family (CiteKind) show "
            "coverage + the resolved/statute_only/ambiguous/open/unsupported/broken "
            "fractions (Pro 'how to judge success' view). Auto-on under --mode both. "
            "Additive: does not change precision/recall output or the --json schema."
        ),
    )

    # --- surface-lints ---
    surface_lints_p = sub.add_parser(
        "surface-lints",
        help="corpus-wide Legal Surface Graph lint report + node-kind census (fi)",
        description=(
            "Parse-only, replay-free lint report from the Legal Surface Graph (the "
            "analyzer OUTPUT of the surface-graph spine; the lint-layer counterpart "
            "to `refs-bench`/`parse-bench`). Reads each statute's cached body XML, "
            "builds the surface graph and derives the surface lints READ-ONLY (no "
            "oracle replay, no apply, no diff), then tallies per-lint_kind counts, "
            "per-severity counts, a graph node-kind CENSUS, and the top-N statutes "
            "by lint count with example messages. Loads the statute-name + EU "
            "registries ONCE per worker so resolution-dependent reference lints "
            "fire (notes degraded mode if the artifact is absent). A statute that "
            "errors is counted in an errored bucket by id, never silently skipped. "
            "Use --limit N to sample cheaply."
        ),
    )
    surface_lints_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap the corpus to the first N statutes (default: no cap = full corpus)",
    )
    surface_lints_p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="worker process count (default: min(8, cpu-2) to respect memory ceiling)",
    )
    surface_lints_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="worklist depth: top N statutes / errored statutes shown (default: 20)",
    )
    surface_lints_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with lint_kind/severity counts, node-kind census, top statutes",
    )

    # --- broken-refs ---
    broken_refs_p = sub.add_parser(
        "broken-refs",
        help="corpus broken-reference report (fi); current-state default, replay opt-in",
        description=(
            "Corpus dangling-reference (full-accounting) report: per citing "
            "statute, extract resolved cross-statute citations and flag two BROKEN "
            "kinds. (1) PROVISION absent: the cited section/momentti does not exist "
            "in the target statute's text-state. (2) TARGET STATUTE not in force: "
            "the cited ACT itself was repealed (its registry/oracle `valid_to` is "
            "past) — a live consolidated text still pointing at a dead act. "
            "DEFAULT (current-state, no replay): provision presence is checked "
            "against the target's CURRENT consolidated body and the statute "
            "lifecycle against the current date (the Finlex oracle gives both for "
            "free), so it is a cheap structural check that runs corpus-wide "
            "without timing out. An unknown target lifecycle is reported as "
            "UNVERIFIABLE (fail-loud), never broken. "
            "--provenance: adds the temporal premium via point-in-time `legal_pit` "
            "replay of the TARGET trees as of the citation AND now, classifying the "
            "disappearance (repealed_since / renumbered_since / never_existed) — "
            "this is SLOW, use --limit to sample. A target whose body/tree cannot "
            "be materialized is reported as UNAVAILABLE (fail-loud), never silently "
            "dropped and never called broken. Surface-fact discipline: a finding is "
            "'the cited target provision is absent/renumbered in the target's "
            "text-state', NOT a legal conclusion about the law's validity."
        ),
    )
    broken_refs_p.add_argument(
        "--provenance",
        action="store_true",
        help=(
            "opt into the heavy point-in-time replay path for the temporal "
            "classification (repealed_since/renumbered_since/never_existed); "
            "default OFF = fast current-state presence scan, no replay"
        ),
    )
    broken_refs_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap the corpus to the first N citing statutes (default: no cap)",
    )
    broken_refs_p.add_argument(
        "--stride",
        type=int,
        default=0,
        help=(
            "scan every Nth citing statute (representative corpus-wide sample "
            "instead of a contiguous prefix); applied before --limit (default: "
            "off = every statute)"
        ),
    )
    broken_refs_p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="worker process count (default: min(8, cpu-2) to respect memory ceiling)",
    )
    broken_refs_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="worklist depth: top N statutes / errored statutes shown (default: 20)",
    )
    broken_refs_p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON with findings-by-reason, unavailable counts, top statutes",
    )
    broken_refs_p.add_argument(
        "--ledger-out",
        dest="ledger_out",
        default="",
        metavar="PATH",
        help=(
            "write the COMPLETE dangling-reference ledger (every "
            "target_statute_repealed finding: citing statute, source span, dead "
            "target, repeal date) to PATH as JSON (or .md alongside if PATH ends "
            ".md). Current-state mode only. Independent of --json/--top (which "
            "summarize); --ledger-out is the full täyslaskenta dump."
        ),
    )

    # --- dangling-refs ---
    dangling_refs_p = sub.add_parser(
        "dangling-refs",
        help=(
            "corpus DANGLING-reference report over the published fi_refs "
            "projection (fi); three-way PRESENT/DANGLING/EXISTENCE_UNKNOWN"
        ),
        description=(
            "Read-only projection over the published fi_refs artifact: classify "
            "every RESOLVED cross-reference (cite_confidence exact/approximate — a "
            "reference asserting a specific target provision) into a CLOSED "
            "three-way existence status against the target act's CURRENT "
            "consolidated text-state (as-of-NOW; no replay). PRESENT = the cited "
            "provision resolves; DANGLING = the act is materialized but the cited "
            "provision resolves to nothing; EXISTENCE_UNKNOWN = existence could "
            "NOT be determined (target act absent from corpus, body not "
            "materialized, or no statute identity). TAG-DON'T-GUESS: an "
            "EXISTENCE_UNKNOWN is an honest non-determination, NEVER reported as "
            "DANGLING. Non-resolved references (statute_only/ambiguous/open/...) "
            "are out of scope and counted separately. The as-of-NOW vs as-of-"
            "citing distinction is the declared residual (the heavier "
            "broken-refs --provenance path does the as-of-citing replay). Surface "
            "fact, not a legal conclusion."
        ),
    )
    dangling_refs_p.add_argument(
        "--fi-refs",
        dest="fi_refs",
        default=None,
        metavar="PATH",
        help=(
            "path to the fi_refs projection (.jsonl or .parquet). Default: the "
            "export's standard output location under .tmp/projections/ or "
            "data/fi/v1/."
        ),
    )
    dangling_refs_p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="write the full typed report (counts + every DANGLING witness) to PATH as JSON",
    )
    dangling_refs_p.add_argument(
        "--top",
        type=int,
        default=20,
        help="number of DANGLING witnesses shown in the text summary (default: 20)",
    )
    dangling_refs_p.add_argument(
        "--json",
        action="store_true",
        help="emit the full typed report as JSON to stdout",
    )

    # --- cross-ref-report ---
    cross_ref_report_p = sub.add_parser(
        "cross-ref-report",
        help=(
            "render the dangling cross-reference claim as a neutral, "
            "independently-verifiable Markdown document (fi)"
        ),
        description=(
            "PRESENTATION of the existing dangling-reference claim "
            "(lawvm.fi.reference.dangling.v1) as a structured Markdown report a "
            "legal scholar / Finlex maintainer / journalist can read and check. "
            "Either runs the claim fresh over the fi_refs projection (--fi-refs) "
            "or renders a saved `dangling-refs --out` JSON (--report-json). Adds "
            "NO new computation and NO new authority: it reads the typed report's "
            "counts and DANGLING witnesses verbatim and lays them out with a "
            "prominent methodology/limits section, deterministic findings grouped "
            "by target act (no silent truncation — a capped list states 'showing "
            "top N of M'), and reproducible verification steps. EXISTENCE_UNKNOWN "
            "rows are excluded from the findings (honest non-determination, never "
            "reported as broken). A dangling reference is a textual/maintenance "
            "fact, not a legal conclusion."
        ),
    )
    cross_ref_report_p.add_argument(
        "--fi-refs",
        dest="fi_refs",
        default=None,
        metavar="PATH",
        help=(
            "path to the fi_refs projection (.jsonl or .parquet) to run the claim "
            "over. Default: the export's standard output location. Ignored when "
            "--report-json is given."
        ),
    )
    cross_ref_report_p.add_argument(
        "--report-json",
        dest="report_json",
        default=None,
        metavar="PATH",
        help=(
            "render a previously saved `dangling-refs --out` JSON report instead "
            "of recomputing (re-asserts the report's totality/closed-status guards "
            "on read)"
        ),
    )
    cross_ref_report_p.add_argument(
        "--scope-label",
        dest="scope_label",
        default=None,
        metavar="TEXT",
        help=(
            "free-text declaration of the corpus slice this run covers, printed "
            "prominently so a slice is never mistaken for the whole corpus"
        ),
    )
    cross_ref_report_p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="write the Markdown report to PATH (default: stdout)",
    )
    cross_ref_report_p.add_argument(
        "--top",
        type=int,
        default=None,
        help=(
            "max number of dangling witnesses rendered inline (the full count is "
            "always stated; default: 200)"
        ),
    )

    # --- surface-graph ---
    surface_graph_p = sub.add_parser(
        "surface-graph",
        help="end-to-end Legal Surface Graph inspector for one statute (fi)",
        description=(
            "Build the FULL Legal Surface Graph for one statute (all 8 lenses -> "
            "assembler -> cross-lens/frame edge passes -> lints) and print a single "
            "view of the middle semantics: lens coverage, node-kind and edge-kind "
            "census (the interlink fabric is flagged), the reference "
            "resolution-status breakdown (resolved / statute_only / ambiguous / "
            "open / broken), and the derived lints. READ-ONLY, surface-fact only — "
            "the authority firewall holds (every node/edge is surface_only); this "
            "is a projection of what the graph knows, never a legal conclusion."
        ),
    )
    surface_graph_p.add_argument(
        "statute_id",
        help="statute id to inspect, e.g. 527/2014",
    )
    surface_graph_p.add_argument(
        "--json",
        action="store_true",
        help="emit the graph summary as JSON",
    )

    # --- corpus-graph ---
    corpus_graph_p = sub.add_parser(
        "corpus-graph",
        help="export the cross-statute corpus Legal Surface Graph (fi)",
        description=(
            "Build the CROSS-STATUTE corpus Legal Surface Graph over a DECLARED "
            "corpus slice (--ids or --limit; never a silent full-corpus "
            "truncation) and export a typed artifact: the node set + edge set "
            "(each edge carrying edge_kind, endpoints, provenance, resolution "
            "status, and the surface_only firewall flag) plus a census (node-kind "
            "/ edge-kind counts, the cross-statute interlink fabric, the "
            "resolution-status breakdown, and the count of genuinely inter-statute "
            "reference edges). The same cited target collapses to ONE shared "
            "entity node, so 'what cites this act/provision' is a graph query. "
            "READ-ONLY, surface-fact only — the authority firewall holds (every "
            "node/edge is surface_only); a fail-loud merge (same node id + "
            "divergent payload RAISES) and tag-don't-guess (ambiguous -> "
            "has_candidate, never an invented target). Backed by the claim "
            "lawvm.fi.legal_surface_graph.v1."
        ),
    )
    corpus_graph_p.add_argument(
        "--ids",
        default=None,
        help="explicit comma-separated statute ids to build the slice over "
        "(takes precedence over --limit)",
    )
    corpus_graph_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="build over the first N statute ids of the corpus (required if "
        "--ids is not given; the full build is heavy, so the scope must be "
        "declared explicitly)",
    )
    corpus_graph_p.add_argument(
        "--surface-time",
        dest="surface_time",
        default=None,
        help="as-of surface time for resolution (optional)",
    )
    corpus_graph_p.add_argument(
        "--out",
        default=None,
        help="write the full export artifact (JSON) to this path",
    )
    corpus_graph_p.add_argument(
        "--json",
        action="store_true",
        help="emit the full export artifact as JSON to stdout",
    )

    # --- parse-characterize ---
    parse_char_p = sub.add_parser(
        "parse-characterize",
        help="snapshot/verify the johtolause parser's behavior (rewrite oracle)",
        description=(
            "Characterization golden corpus for the johtolause parser: snapshot "
            "the canonical op codes the parser produces TODAY (bugs included) for "
            "every amendment johtolause, labeled clean/known-drop by parse-bench. "
            "`verify` re-runs and diffs vs a saved golden, reporting regressions "
            "(a clean row whose ops changed) and fixes (a known-drop row now "
            "clean) — the safety net that makes a parser rewrite mechanical."
        ),
    )
    parse_char_p.add_argument(
        "characterize_cmd",
        choices=["snapshot", "verify"],
        help="snapshot: write the golden corpus; verify: diff current behavior vs a saved golden",
    )
    parse_char_p.add_argument(
        "--out",
        default="",
        help="snapshot output path (default: data/finland/parse_characterization_golden.jsonl)",
    )
    parse_char_p.add_argument(
        "--golden",
        default="",
        help="verify: path to the saved golden corpus to diff against",
    )
    parse_char_p.add_argument("--limit", type=int, default=0, help="cap to first N statutes")
    parse_char_p.add_argument("--workers", type=int, default=0, help="worker processes (default 8)")
    parse_char_p.add_argument("--json", action="store_true", help="verify: emit JSON diff")

    # --- build-statute-name-registry ---
    bsnr_p = sub.add_parser(
        "build-statute-name-registry",
        help="materialize the full statute-name -> id registry artifact (Index B)",
        description=(
            "Enumerate ALL statutes in the farchive corpus, read each docTitle and "
            "its real enactment date, and serialize the full statute-name -> id "
            "registry (Index B) to a JSON-lines artifact for the reference "
            "resolution projection. Memory-careful streaming enumeration "
            "(~56k statutes). valid_from = FRBR dateIssued (open when absent); "
            "valid_to is always open (never fabricated); titles with no known "
            "statute head are indexed nominative-only (no guessed inflection). "
            "The artifact is regenerable + large, so it is gitignored, not "
            "committed (like the .farchive it derives from)."
        ),
    )
    bsnr_p.add_argument(
        "--out",
        default="",
        help=(
            "output path (default: "
            "$LAWVM_CANONICAL_DATA_ROOT/data/finland/statute_name_registry.jsonl)"
        ),
    )
    bsnr_p.add_argument(
        "--limit", type=int, default=0, help="cap to first N statutes (for testing)"
    )

    # --- rebuild-indexes ---
    ri_p = sub.add_parser(
        "rebuild-indexes",
        help="regenerate Tier 2 Parquet projections from Tier 1 farchive",
        description=(
            "Regenerate Tier 2 Parquet+zstd projections "
            "(data/{jurisdiction}/{schema-version}/*.parquet) from the current "
            "Tier 1 farchive state. Incremental mode (default) skips projections "
            "whose state files show they are already up-to-date. Full mode "
            "unconditionally regenerates all projections. "
            "See TIER_2_STORAGE_ARCHITECTURE.md for the three-tier model."
        ),
        parents=_P,
    )
    ri_mode = ri_p.add_mutually_exclusive_group()
    ri_mode.add_argument(
        "--incremental",
        action="store_true",
        default=False,
        help="only regenerate stale projections (default behaviour)",
    )
    ri_mode.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="discard incremental state and regenerate all projections",
    )
    ri_p.add_argument(
        "--check",
        action="store_true",
        default=False,
        help=(
            "report projection freshness vs the source farchive and exit "
            "non-zero if any are stale; does NOT rebuild"
        ),
    )
    ri_p.add_argument(
        "--workers",
        type=int,
        default=0,
        metavar="N",
        help="parallel workers (default: cpu_count - 2)",
    )
    ri_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        metavar="DIR",
        help="root data directory containing farchives (default: data)",
    )
    ri_p.add_argument(
        "--schema-version",
        dest="schema_version",
        default=None,
        metavar="SV",
        help="Tier 2 schema version (default: v1)",
    )
    ri_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-projection progress",
    )

    # --- build-index-db ---
    bid_p = sub.add_parser(
        "build-index-db",
        help="compose Tier 2 Parquets into a single DuckDB .db file",
        description=(
            "Wraps each .parquet in the Tier 2 directory as a DuckDB view inside "
            "a single portable .db file. Optionally builds FTS indexes on text "
            "columns. The .db is suitable for portable single-file consumers. "
            "Run 'lawvm rebuild-indexes' first to generate the Parquet projections."
        ),
        parents=_P,
    )
    bid_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        metavar="DIR",
        help="root data directory (default: data)",
    )
    bid_p.add_argument(
        "--out",
        dest="out",
        default=None,
        metavar="DB_PATH",
        help=(
            "output .db file path "
            "(default: data/{jurisdiction}/{schema-version}/lawvm.db)"
        ),
    )
    bid_p.add_argument(
        "--fts",
        action="store_true",
        help=(
            "build DuckDB FTS index on section text and fi_he_atoms text_content "
            "for use with 'lawvm topic --mode fts'"
        ),
    )
    bid_p.add_argument(
        "--schema-version",
        dest="schema_version",
        default=None,
        metavar="SV",
        help="Tier 2 schema version to compose (default: v1)",
    )
    bid_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-table progress and row counts",
    )

    # --- topic ---
    topic_p = sub.add_parser(
        "topic",
        help="keyword / full-text search across statute sections and HE body atoms",
        description=(
            "Search statute section text (sections.parquet replay_text) and "
            "HE body atoms (fi_he_atoms.parquet text_content) for a keyword "
            "or full-text query. "
            "keyword mode (default): case-insensitive substring match. "
            "fts mode: DuckDB FTS extension; requires 'lawvm build-index-db --fts'."
        ),
        parents=_P,
    )
    topic_p.add_argument(
        "--topic",
        required=True,
        metavar="STRING",
        help="search term or phrase",
    )
    topic_p.add_argument(
        "--mode",
        default="keyword",
        choices=["keyword", "fts"],
        help="search mode: keyword (default, ILIKE match) or fts (DuckDB FTS index)",
    )
    topic_p.add_argument(
        "--statute-filter",
        dest="statute_filter",
        metavar="PATTERN",
        help="filter results to statute IDs matching this glob pattern (e.g. '7*/202*')",
    )
    topic_p.add_argument(
        "--db-path",
        dest="db_path",
        default="data/fi/v1/lawvm.db",
        metavar="DB_PATH",
        help="DuckDB .db file for fts mode (default: data/fi/v1/lawvm.db)",
    )
    topic_p.add_argument("--as-of", dest="as_of", metavar="DATE",
                         help="filter records valid at DATE (YYYY-MM-DD; best-effort for sections)")
    topic_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    topic_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="directory containing sections.parquet and fi_he_atoms.parquet (default: data/fi/v1)",
    )
    topic_p.add_argument(
        "-o", "--output-format", dest="output_format", default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )
    topic_p.add_argument(
        "--source-filter",
        dest="source_filter_kind",
        default="both",
        choices=["statutes", "hes", "both"],
        help=(
            "filter search sources: 'statutes' = enacted statute sections only "
            "(fi_sections_text.parquet), 'hes' = HE atoms only (fi_he_atoms.parquet), "
            "'both' = all sources (default)"
        ),
    )

    # --- follow-refs ---
    fr_p = sub.add_parser(
        "follow-refs",
        help="multi-hop reference traversal from a provision (backed by fi_refs.parquet)",
        description=(
            "Traverse the citation graph from --start up to --depth hops. "
            "Returns an edge list annotated with depth and direction. "
            "Backed by fi_refs.parquet."
        ),
        parents=_P,
    )
    fr_p.add_argument(
        "--start",
        required=True,
        metavar="PROVISION_REF",
        help="starting provision reference, e.g. '711/2022' or '711/2022/7'",
    )
    fr_p.add_argument(
        "--depth",
        type=int,
        default=1,
        metavar="N",
        help="number of hops to traverse (default: 1)",
    )
    fr_p.add_argument(
        "--direction",
        default="forward",
        choices=["forward", "reverse", "both"],
        help="traversal direction: forward (outgoing), reverse (incoming), both (default: forward)",
    )
    fr_p.add_argument(
        "--include-broken",
        dest="include_broken",
        action="store_true",
        help="include references with cite_confidence='broken'",
    )
    fr_p.add_argument("--as-of", dest="as_of", metavar="DATE",
                      help="filter reference validity to DATE (YYYY-MM-DD)")
    fr_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    fr_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="directory containing fi_refs.parquet (default: data/fi/v1)",
    )
    fr_p.add_argument(
        "-o", "--output-format", dest="output_format", default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )

    # --- pit-timeline ---
    pt_p = sub.add_parser(
        "pit-timeline",
        help="provision amendment history (index-backed; see 'timeline' for live PIT replay)",
        description=(
            "Show amendment operations affecting a provision, backed by ops.parquet. "
            "Filters by statute ID and optionally by section and date range. "
            "For live PIT replay, use 'lawvm timeline <statute_id>'."
        ),
        parents=_P,
    )
    pt_p.add_argument(
        "--provision",
        required=True,
        metavar="PROVISION_REF",
        help="provision reference: statute_id (e.g. '2002/738') or section ref (e.g. '2002/738/7')",
    )
    pt_p.add_argument(
        "--since",
        metavar="DATE",
        help="only show amendments from this year onwards (YYYY-MM-DD)",
    )
    pt_p.add_argument(
        "--until",
        metavar="DATE",
        help="only show amendments up to this year (YYYY-MM-DD)",
    )
    pt_p.add_argument(
        "--include-amendments",
        dest="include_amendments",
        action="store_true",
        help="include HE/source act information (requires fi_he_law_refs)",
    )
    pt_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    pt_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="directory containing ops.parquet (default: data/fi/v1)",
    )
    pt_p.add_argument(
        "-o", "--output-format", dest="output_format", default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )

    # --- pit-diff ---
    pd_p = sub.add_parser(
        "pit-diff",
        help="provision text + structural diff between two PIT states (index-backed)",
        description=(
            "Show amendment ops affecting a provision between t1 and t2, "
            "backed by ops.parquet. Optionally includes current section text "
            "(sections.parquet) and reference state diff (fi_refs.parquet). "
            "For per-provision replay-vs-oracle diff, use 'lawvm diff <statute_id>'."
        ),
        parents=_P,
    )
    pd_p.add_argument(
        "--provision",
        required=True,
        metavar="PROVISION_REF",
        help="provision reference, e.g. '2002/738' or '2002/738/7'",
    )
    pd_p.add_argument(
        "--t1",
        required=True,
        metavar="DATE",
        help="start date (YYYY-MM-DD)",
    )
    pd_p.add_argument(
        "--t2",
        required=True,
        metavar="DATE",
        help="end date (YYYY-MM-DD)",
    )
    pd_p.add_argument(
        "--include-text",
        dest="include_text",
        action="store_true",
        help="include current section text from sections.parquet",
    )
    pd_p.add_argument(
        "--include-refs",
        dest="include_refs",
        action="store_true",
        help="include reference changes from fi_refs.parquet valid_at intervals",
    )
    pd_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    pd_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="directory containing ops.parquet (default: data/fi/v1)",
    )
    pd_p.add_argument(
        "-o", "--output-format", dest="output_format", default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )

    # --- telos ---
    telos_p = sub.add_parser(
        "telos",
        help="query telos/purpose sections from sections.parquet (feature #5)",
        description=(
            "Return purpose/telos sections (is_purpose_section=true) for a statute "
            "or all statutes, backed by sections.parquet. "
            "Requires telos-section flag (feature #5) to have been applied to the export."
        ),
        parents=_P,
    )
    telos_p.add_argument(
        "--statute",
        metavar="STATUTE_ID",
        help="filter to one statute, e.g. '711/2022'",
    )
    telos_p.add_argument("--as-of", dest="as_of", metavar="DATE",
                         help="best-effort temporal filter (YYYY-MM-DD)")
    telos_p.add_argument("--limit", type=int, metavar="N", help="limit output rows")
    telos_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="directory containing sections.parquet (default: data/fi/v1)",
    )
    telos_p.add_argument(
        "-o", "--output-format", dest="output_format", default="table",
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )

    # --- simulate (feature #8) ---
    simulate_p = sub.add_parser(
        "simulate",
        help="simulate a branch (HE) applied to current enacted state (feature #8)",
        description=(
            "Materialize a hypothetical PIT state by applying an HE's proposed_ops "
            "over the current enacted statute state and computing the structural delta. "
            "Requires fi_government_proposal.farchive (feature #0) or "
            "fi_he_branch_ops.parquet (feature #8 projection) to be available."
        ),
    )
    simulate_p.add_argument(
        "--branch",
        required=True,
        metavar="BRANCH_ID",
        help="branch ID to simulate, e.g. 'fi/he/2024/184'",
    )
    simulate_p.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        help=(
            "simulated date (YYYY-MM-DD); default: HE proposed_voimaantulo if known, "
            "else today"
        ),
    )
    simulate_p.add_argument(
        "--diff-from",
        dest="diff_from",
        metavar="BASELINE",
        default="current",
        help="baseline to diff against: 'current', 'baseline', or a YYYY-MM-DD date (default: current)",
    )
    simulate_p.add_argument(
        "--detect-broken-refs",
        dest="detect_broken_refs",
        action="store_true",
        help=(
            "flag refs in other statutes that fail to resolve in simulated state "
            "(composes fi_refs.parquet, feature #1)"
        ),
    )
    simulate_p.add_argument(
        "--detect-actor-changes",
        dest="detect_actor_changes",
        action="store_true",
        help=(
            "flag actor mentions added/removed by branch ops "
            "(composes fi_actors.parquet, feature #2)"
        ),
    )
    simulate_p.add_argument(
        "--scope",
        metavar="PROVISION_REF",
        help="narrow simulation to provisions matching this prefix, e.g. '711/2022'",
    )
    simulate_p.add_argument(
        "--strict",
        action="store_true",
        help="reject branches with PARTIAL parse status (strict mode)",
    )
    simulate_p.add_argument(
        "-o", "--output-format",
        dest="output_format",
        default="json",
        choices=["table", "json", "jsonl"],
        help="output format (default: json)",
    )
    simulate_p.add_argument(
        "--farchive",
        metavar="PATH",
        help="path to fi_government_proposal.farchive (default: data/fi_government_proposal.farchive)",
    )
    simulate_p.add_argument(
        "--refs-parquet",
        dest="refs_parquet",
        metavar="PATH",
        help="path to fi_refs.parquet for broken-ref detection (feature #1)",
    )
    simulate_p.add_argument(
        "--actors-parquet",
        dest="actors_parquet",
        metavar="PATH",
        help="path to fi_actors.parquet for actor-change detection (feature #2)",
    )
    simulate_p.add_argument(
        "--branch-ops-parquet",
        dest="branch_ops_parquet",
        metavar="PATH",
        help="path to fi_he_branch_ops.parquet (feature #8 projection)",
    )
    simulate_p.add_argument(
        "--debug-parse",
        dest="debug_parse",
        action="store_true",
        help="include full parse_findings list in output (diagnostic mode)",
    )

    # --- export-fi-he-branch-ops (feature #8) ---
    efbo_p = sub.add_parser(
        "export-fi-he-branch-ops",
        help="export fi_he_branch_ops.parquet from fi_government_proposal.farchive (feature #8)",
        description=(
            "Parse HE amendment-proposal sections from fi_government_proposal.farchive "
            "and write typed proposed_ops to data/fi/v1/fi_he_branch_ops.parquet."
        ),
    )
    efbo_p.add_argument(
        "--farchive",
        metavar="PATH",
        default="data/fi_government_proposal.farchive",
        help="path to fi_government_proposal.farchive (default: data/fi_government_proposal.farchive)",
    )
    efbo_p.add_argument(
        "--data-dir",
        dest="data_dir",
        metavar="DIR",
        default="data/fi/v1",
        help="output directory for Parquet file (default: data/fi/v1)",
    )
    efbo_p.add_argument("--limit", type=int, metavar="N", help="process only first N HEs (debug)")
    efbo_p.add_argument(
        "--year-range",
        dest="year_range",
        metavar="Y1:Y2",
        help="filter to HE years Y1–Y2 inclusive, e.g. '2020:2024'",
    )
    efbo_p.add_argument("--strict", action="store_true", help="abort on first parse failure")
    efbo_p.add_argument("--verbose", "-v", action="store_true", help="print per-HE progress")
    efbo_p.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="parse but do not write Parquet output")

    # --- claim --- (manual compilation claims, Slices 1+2)
    claim_p = sub.add_parser(
        "claim",
        help="operator CLI for manual compilation claims (Slices 1+2)",
        description=(
            "Manage manual compilation claims: propose, accept, reject, retract, "
            "list, and show claims. State transitions are recorded in events.jsonl. "
            "Storage: data/fi/v1/manual_claims/ by default."
        ),
    )
    claim_p.add_argument(
        "--data-dir",
        dest="data_dir",
        default="data/fi/v1",
        metavar="DIR",
        help="base data directory (default: data/fi/v1); manual_claims/ is appended",
    )
    claim_p.add_argument(
        "--graph-store-root", dest="graph_store_root", metavar="PATH", default=None,
        help="path to provenance graph store (default: data/fi/v1/provenance_graph/)",
    )
    claim_sub = claim_p.add_subparsers(dest="claim_subcommand", metavar="<subcommand>")

    # propose
    claim_propose_p = claim_sub.add_parser(
        "propose",
        help="file a new claim from a JSON file",
    )
    claim_propose_p.add_argument(
        "--claim-file", dest="claim_file", required=True, metavar="FILE",
        help="path to claim JSON file",
    )
    claim_propose_p.add_argument(
        "--validator",
        choices=["span", "entailment", "all"],
        default=None,
        help="run validator(s) as part of proposal (default: none)",
    )

    # accept
    claim_accept_p = claim_sub.add_parser(
        "accept",
        help="accept a proposed claim (marks review_status=human_reviewed)",
    )
    claim_accept_p.add_argument("claim_id", help="claim ID (full SHA-256 hex)")

    # reject
    claim_reject_p = claim_sub.add_parser(
        "reject",
        help="reject a proposed claim",
    )
    claim_reject_p.add_argument("claim_id", help="claim ID (full SHA-256 hex)")
    claim_reject_p.add_argument("--reason", required=True, help="reason for rejection")

    # retract
    claim_retract_p = claim_sub.add_parser(
        "retract",
        help="retract an accepted claim (taint report is Slice 5)",
    )
    claim_retract_p.add_argument("claim_id", help="claim ID (full SHA-256 hex)")
    claim_retract_p.add_argument("--reason", required=True, help="reason for retraction")

    # list
    claim_list_p = claim_sub.add_parser(
        "list",
        help="list claims with optional filters",
    )
    claim_list_p.add_argument("--kind", metavar="CLAIM_KIND",
                               help="filter by claim kind, e.g. fi.v1.INLINE_STATUTE_RESOLUTION")
    claim_list_p.add_argument("--layer", choices=["substrate", "extraction", "correction", "adjudication"],
                               help="filter by claim layer")
    claim_list_p.add_argument("--review-status", dest="review_status",
                               choices=["proposed", "second_pass_correlated", "human_reviewed"],
                               help="filter by review status")
    claim_list_p.add_argument("--status",
                               choices=["proposed", "accepted", "rejected", "retracted",
                                        "superseded", "orphaned", "needs_revalidation"],
                               help="filter by lifecycle status")
    claim_list_p.add_argument(
        "--has-attestation-kind",
        dest="has_attestation_kind",
        metavar="KIND",
        default=None,
        help="filter by graph attestation kind, e.g. reviewed or entailment_verified",
    )

    # show
    claim_show_p = claim_sub.add_parser(
        "show",
        help="show all four records for a claim (payload, state, events, composition decisions)",
    )
    claim_show_p.add_argument("claim_id", help="claim ID (full SHA-256 hex)")
    claim_show_p.add_argument(
        "--profile",
        choices=[
            "default",
            "fi_strict_with_attested_reference_resolution",
            "strict",
            "fi_strict",
            "deterministic_only",
        ],
        default=None,
        help="authorization profile for the read-only claim show result",
    )

    # validate (standalone validator re-run)
    claim_validate_p = claim_sub.add_parser(
        "validate",
        help="re-run validators on an already-filed claim",
    )
    claim_validate_p.add_argument("claim_id", help="claim ID (full SHA-256 hex)")
    claim_validate_p.add_argument(
        "--validator",
        choices=["span", "entailment", "all"],
        default="all",
        help="which validator(s) to run (default: all)",
    )

    # taint-report (Slice 5)
    claim_taint_p = claim_sub.add_parser(
        "taint-report",
        help="show taint reports for retracted claims (Slice 5)",
    )
    claim_taint_p.add_argument(
        "claim_id", nargs="?", default=None,
        help="claim ID to show taint report for (omit with --list or --build)",
    )
    claim_taint_p.add_argument(
        "--list", action="store_true", dest="list",
        help="list all taint reports",
    )
    claim_taint_p.add_argument(
        "--build", metavar="BUILD_ID", dest="build", default=None,
        help="show all taint reports affecting a specific build",
    )

    # propose-claims (Slice 4) — top-level command
    propose_claims_p = sub.add_parser(
        "propose-claims",
        help="LLM-aided claim proposal pipeline (Slice 4)",
    )
    propose_claims_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="base data directory",
    )
    propose_claims_p.add_argument(
        "--from-frontier", action="store_true", dest="from_frontier",
        help="propose claims for frontier rows (NULL target_statute_id slots)",
    )
    propose_claims_p.add_argument(
        "--gap-discovery", action="store_true", dest="gap_discovery",
        help="scan HE body for plain-text citations not in deterministic output",
    )
    propose_claims_p.add_argument(
        "--he", metavar="HE_ID", dest="he", default=None,
        help="HE ID for gap-discovery or specific gap rescue",
    )
    propose_claims_p.add_argument(
        "--kind", metavar="CLAIM_KIND", default="fi.v1.INLINE_STATUTE_RESOLUTION",
        help="claim kind to propose (default: fi.v1.INLINE_STATUTE_RESOLUTION)",
    )
    propose_claims_p.add_argument(
        "--limit", type=int, default=100,
        help="max proposals per invocation (default: 100)",
    )
    propose_claims_p.add_argument(
        "--max-claims-no-cap", action="store_true", dest="max_claims_no_cap",
        help="remove the --limit cap (adversary #2 guard)",
    )
    propose_claims_p.add_argument(
        "--backend", choices=["mock", "qwen"], default="mock",
        help="LLM backend to use (default: mock)",
    )
    propose_claims_p.add_argument(
        "--graph-store-root", dest="graph_store_root", metavar="PATH", default=None,
        help="path to provenance graph store (default: data/fi/v1/provenance_graph/); smoke-run isolation",
    )
    propose_claims_p.add_argument(
        "--claim-store-root", dest="graph_store_root", metavar="PATH",
        help=argparse.SUPPRESS,
    )
    propose_claims_p.add_argument(
        "--frontier-source",
        dest="frontier_source",
        choices=["inline_citations", "fi_refs", "deterministic_refs"],
        default="inline_citations",
        help=(
            "which parquet to scan for unresolved rows (default: inline_citations). "
            "inline_citations: fi_inline_citations.parquet NULL canonical_id rows. "
            "fi_refs: fi_refs.parquet NULL target_statute_id rows (legacy; returns 0 rows on real corpus). "
            "deterministic_refs: fi_refs__deterministic_only.parquet unresolved span-keyed rows."
        ),
    )

    # validate-claims (Slice 4) — top-level command
    validate_claims_p = sub.add_parser(
        "validate-claims",
        help="run validators on proposed claims (Slice 4)",
    )
    validate_claims_p.add_argument(
        "--data-dir", dest="data_dir", default="data/fi/v1",
        help="base data directory",
    )
    validate_claims_p.add_argument(
        "--claim-id", dest="claim_id", metavar="CLAIM_ID", default=None,
        help="validate one specific claim",
    )
    validate_claims_p.add_argument(
        "--all", action="store_true", dest="all",
        help="validate all filed claims",
    )
    validate_claims_p.add_argument(
        "--kind", metavar="CLAIM_KIND", default=None,
        help="filter by claim kind (with --all)",
    )
    validate_claims_p.add_argument(
        "--status", metavar="STATUS", default=None,
        help="filter by lifecycle status (with --all)",
    )
    validate_claims_p.add_argument(
        "--graph-store-root", dest="graph_store_root", metavar="PATH", default=None,
        help="path to provenance graph store (default: data/fi/v1/provenance_graph/)",
    )

    # --- export-transition-graph ---
    export_tg_p = sub.add_parser(
        "export-transition-graph",
        parents=_P,
        help="export a certified transition graph (SQLite) for the selected jurisdiction",
        description=(
            "Run the selected jurisdiction replay adapter once for a statute and emit a "
            "self-contained SQLite database of certified L3 tree transitions, "
            "per-change-date engine oracle checkpoints, and content blobs. The "
            "Python engine is the only authority; the export lets a browser "
            "render and optionally fold certified patches without resolving "
            "legal targets in JS."
        ),
    )
    export_tg_p.add_argument(
        "--statute",
        required=True,
        metavar="ID",
        help="statute id, canonical 'num/year' (e.g. 301/2004) or 'year/num'",
    )
    export_tg_p.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="output SQLite db path",
    )
    export_tg_p.add_argument(
        "--slice",
        dest="slice",
        default="",
        metavar="ADDRESS_PREFIX",
        help="optional address-prefix slice (e.g. chapter:11); default = whole act",
    )
    export_tg_p.add_argument(
        "--granularity",
        dest="granularity",
        default="subsection",
        choices=["subsection", "section", "chapter"],
        help=(
            "covering-frontier depth for transitions: 'subsection' (default) "
            "emits section/subsection-granular transitions; 'section' tiles at "
            "section depth; 'chapter' is the legacy whole-chapter fallback"
        ),
    )

    # --- export-markdown-git ---
    export_md_git_p = sub.add_parser(
        "export-markdown-git",
        parents=_P,
        help="stream LawVM Markdown snapshots as a git fast-import stream",
        description=(
            "Materialize selected adapter-backed LawVM statutes, render act-level "
            "GitHub-compatible Markdown, and emit a git fast-import stream with "
            "one commit per effective date. By default the stream is written to "
            "stdout for an external consumer such as 'git --git-dir out.git "
            "fast-import --date-format=raw'. The stream writes refs/heads/in-force."
        ),
    )
    export_md_git_p.add_argument(
        "--statute",
        action="append",
        metavar="ID",
        help=(
            "statute id in the selected jurisdiction's canonical form (repeatable). "
            "Default: viewer/statute-timeline-manifest.json"
        ),
    )
    export_md_git_p.add_argument(
        "--manifest",
        default="viewer/statute-timeline-manifest.json",
        metavar="PATH",
        help="manifest used when --statute is omitted (default: viewer/statute-timeline-manifest.json)",
    )
    export_md_git_p.add_argument(
        "--all-replayable",
        action="store_true",
        help="[-j fi] export all Finnish base laws active at least once in the selected timeline, skipping unreplayable statutes",
    )
    export_md_git_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="limit selected statutes after sorting; intended for smoke tests",
    )
    export_md_git_p.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        default=None,
        help="latest effective date to include (default: no cutoff)",
    )
    export_md_git_p.add_argument(
        "--include-future",
        dest="include_future",
        action="store_true",
        default=True,
        help="include prospective future versions when --until is not set (default)",
    )
    export_md_git_p.add_argument(
        "--no-future",
        dest="include_future",
        action="store_false",
        help="cap export at the current date when --until is not set",
    )
    export_md_git_p.add_argument(
        "--timestamp-zone",
        default="UTC",
        metavar="ZONE",
        help="IANA timezone for git author/committer raw dates (default: UTC)",
    )
    export_md_git_p.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="render statutes in N worker processes; N > 1 uses the SQLite spool path",
    )
    export_md_git_p.add_argument(
        "--spool-db",
        default=None,
        metavar="PATH",
        help="write rendered Markdown blobs to a SQLite spool before fast-import",
    )
    export_md_git_p.add_argument(
        "--out",
        default="-",
        metavar="PATH",
        help="write fast-import stream to PATH, or '-' for stdout (default)",
    )
    export_md_git_p.add_argument(
        "--repo",
        default=None,
        metavar="BARE_REPO",
        help="initialize/import directly into a new bare repo instead of writing stdout",
    )
    export_md_git_p.add_argument(
        "--force",
        action="store_true",
        help="remove an existing --repo path before importing",
    )

    # --- certificate-bundle (EXPERIMENTAL) ---
    cert_bundle_p = sub.add_parser(
        "certificate-bundle",
        parents=_P,
        help="EXPERIMENTAL: emit a one-statute certificate bundle fixture (FI)",
        description=(
            "EXPERIMENTAL schema-pressure fixture, NOT a checked certificate. "
            "Run the Finland replay engine once for a statute and emit a "
            "lawvm.certificate.v0 bundle directory (envelope, bundled source "
            "bytes, policy manifests, certified tree-transition trace, "
            "materialization roots, seam projection rows, residual/finding "
            "ledgers, declared coverage). Transitions are derived from "
            "observed state diffs (cert spec section 10 carve-out); no checker "
            "exists, so the output must never be presented as a verified or "
            "checkable public claim."
        ),
    )
    cert_bundle_p.add_argument(
        "--statute",
        default="482/2024",
        metavar="ID",
        help="statute id, canonical 'num/year' (default 482/2024) or 'year/num'",
    )
    cert_bundle_p.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output bundle directory",
    )
    cert_bundle_p.add_argument(
        "--granularity",
        dest="granularity",
        default="subsection",
        choices=["subsection", "section"],
        help="covering-frontier depth (experimental boundary: subsection or section)",
    )
    cert_bundle_p.add_argument(
        "--graph-store-root",
        dest="graph_store_root",
        default=None,
        metavar="DIR",
        help=(
            "provenance graph store for the build-consumption record "
            "(default: $LAWVM_GRAPH_STORE_ROOT, then data/fi/v1/provenance_graph)"
        ),
    )

    # --- BEGIN substrate pack tooling (additive, self-contained) ---
    # Jurisdiction-neutral sparse-pack exporter + offline checker (P3/P4).
    pack_work_p = sub.add_parser(
        "pack-work",
        parents=_P,
        help="export a sparse, content-addressed, certified pack for one work (use -j)",
        description=(
            "Run the selected jurisdiction replay engine once for one work and "
            "emit a sparse, content-addressed, certified substrate pack: deduped "
            "content leaves, sparse selection rows over maximal constant intervals, "
            "certified tree transitions + checkpoints, and a self-describing "
            "PackManifest. The offline 'check-pack' verifier validates it without "
            "running the replay kernel."
        ),
    )
    pack_work_p.add_argument(
        "work_id",
        metavar="WORK_ID",
        help=(
            "work id in strict year-major year/num form (e.g. 2004/301, 1889/39; "
            "sub-numbered 1889/39-001 ok). The Finnish num/year citation form "
            "(301/2004) is rejected — never silently swapped"
        ),
    )
    pack_work_p.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output pack directory",
    )
    pack_work_p.add_argument(
        "--slice",
        dest="slice",
        default="",
        metavar="ADDRESS_PREFIX",
        help="optional address-prefix slice (e.g. chapter:11); default = whole work",
    )
    pack_work_p.add_argument(
        "--granularity",
        dest="granularity",
        default="subsection",
        choices=["subsection", "section", "chapter"],
        help="covering-frontier depth (default: subsection)",
    )

    check_pack_p = sub.add_parser(
        "check-pack",
        parents=_P,
        help="offline-verify a substrate pack directory and print the verdict",
        description=(
            "Read a pack directory produced by 'pack-work' and run the offline, "
            "deterministic substrate checker (L0 integrity + L1 finite-interval "
            "selection algebra). Prints the two-axis verdict. Never runs the "
            "replay engine."
        ),
    )
    check_pack_p.add_argument(
        "pack_dir",
        metavar="DIR",
        help="pack directory to verify",
    )
    check_pack_p.add_argument(
        "--mode",
        choices=["browse", "audit"],
        default="browse",
        help="check mode (default: browse; audit additionally requires source bytes)",
    )

    pack_corpus_p = sub.add_parser(
        "pack-corpus",
        parents=_P,
        help="build a shared-store corpus pack from >=2 single-work pack directories",
        description=(
            "Compose N single-work packs (from 'pack-work') into a corpus pack: "
            "ONE deduped content-leaf base store (the synergy gate's content-leaf "
            "dedup across works) plus an edges/<corpus_version> layer holding "
            "cross-work resolutions. Reads existing packs; never runs replay. The "
            "offline 'check-pack' verifier accepts the result (edges is an optional "
            "layer, so an overlay schema yields VALID_WITH_UNSUPPORTED_LAYERS)."
        ),
    )
    pack_corpus_p.add_argument(
        "member_packs",
        nargs="+",
        metavar="PACK_DIR",
        help="two or more single-work pack directories to compose",
    )
    pack_corpus_p.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output corpus pack directory",
    )
    pack_corpus_p.add_argument(
        "--measure-only",
        action="store_true",
        help="only print the cross-work content-leaf dedup measurement, do not write a pack",
    )

    pack_snapshot_p = sub.add_parser(
        "pack-snapshot",
        help="export a sparse certified pack for one OBSERVED snapshot work (no replay)",
        description=(
            "Pack a static, never-amended observed-codification snapshot through "
            "the substrate WITHOUT running the replay engine: induce the address "
            "tree from the source's section numbering, emit one InitialStateEvent "
            "(genesis observed_codification_snapshot), deduped content leaves, one "
            "selection row per addressable node over a single snapshot date, and "
            "the same self-describing PackManifest 'check-pack' verifies. The "
            "jurisdiction-neutral counterpart to 'pack-work' for the snapshot end "
            "of the uniform object model (the LOCUS / 'any jurisdiction' path)."
        ),
    )
    pack_snapshot_p.add_argument(
        "--source",
        required=True,
        choices=["locus"],
        help="snapshot source adapter (locus = US municipal-code parquet snapshots)",
    )
    pack_snapshot_p.add_argument(
        "--work",
        required=True,
        metavar="STATE/LOCALITY",
        help=(
            "work selector 'STATE/LOCALITY' (e.g. ak/kingcove, ca/san_jose). "
            "Combine with --jurisdiction-type for counties (default: cities)"
        ),
    )
    pack_snapshot_p.add_argument(
        "--jurisdiction-type",
        dest="jurisdiction_type",
        default="cities",
        choices=["cities", "counties"],
        help="LOCUS source_jurisdiction_type (default: cities)",
    )
    pack_snapshot_p.add_argument(
        "--data",
        dest="data_glob",
        default=os.environ.get("LAWVM_LOCUS_DATA_GLOB", ""),
        metavar="GLOB",
        help="LOCUS parquet glob (or set LAWVM_LOCUS_DATA_GLOB)",
    )
    pack_snapshot_p.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output pack directory",
    )
    pack_snapshot_p.add_argument(
        "--no-overlay",
        action="store_true",
        help="exclude the analytical-score overlay layer (legal-state pack only)",
    )
    # --- END substrate pack tooling ---

    # --- BEGIN us_federal jurisdiction tooling (additive, self-contained) ---
    # Thin CLI shims over lawvm.us_federal.*; logic stays in those modules.
    us_import_plaw_p = sub.add_parser(
        "us-import-plaw",
        help="import U.S. Public Law (PLAW) USLM XML zips into the U.S. farchive",
        description=(
            "Import U.S. federal Public Law source units from one or more USLM "
            "XML zip distributions (or URLs) into the canonical U.S. farchive. "
            "Thin shim over lawvm.us_federal.import_plaw."
        ),
    )
    us_import_plaw_p.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="one or more PLAW zip paths or URLs",
    )
    us_import_plaw_p.add_argument(
        "--dest",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: canonical data/us_federal.farchive)",
    )
    us_import_plaw_p.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        help="skip entries already present in the farchive (resume mode)",
    )
    us_import_plaw_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would be imported without writing to the farchive",
    )

    us_import_usc_p = sub.add_parser(
        "us-import-usc",
        help="import U.S. Code annual-edition htm titles into the U.S. farchive",
        description=(
            "Import U.S. Code annual-edition title documents (htm or htm zips, "
            "paths or URLs) into the canonical U.S. farchive. Thin shim over "
            "lawvm.us_federal.import_usc."
        ),
    )
    us_import_usc_p.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="one or more USC title htm/zip paths or URLs",
    )
    us_import_usc_p.add_argument(
        "--dest",
        metavar="PATH",
        default=None,
        help="farchive DB path (default: canonical data/us_federal.farchive)",
    )
    us_import_usc_p.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        help="skip entries already present in the farchive (resume mode)",
    )
    us_import_usc_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would be imported without writing to the farchive",
    )

    us_inventory_p = sub.add_parser(
        "us-inventory",
        help="inventory U.S. federal PLAW source units in the U.S. farchive",
        description=(
            "List U.S. federal Public Law units present in the canonical (or "
            "given) U.S. farchive, with per-Congress counts. Thin shim over "
            "lawvm.us_federal.inventory."
        ),
    )
    us_inventory_p.add_argument(
        "--dest",
        metavar="PATH",
        default=None,
        help="explicit farchive path (default: canonical data/us_federal.farchive)",
    )
    us_inventory_p.add_argument(
        "--congress",
        type=int,
        default=None,
        help="restrict the inventory to a single Congress",
    )
    us_inventory_p.add_argument(
        "--json",
        action="store_true",
        help="emit the inventory as JSON instead of a human summary",
    )

    us_bench_p = sub.add_parser(
        "us-bench",
        help="run the U.S. federal dry-run bench corpus",
        description=(
            "Evaluate the U.S. federal dry-run bench corpus (per-window "
            "witness-anchored coverage). Thin shim over lawvm.us_federal.bench."
        ),
    )
    us_bench_p.add_argument(
        "--corpus",
        metavar="PATH",
        default=None,
        help="bench corpus CSV (default: us/bench/us_bench_corpus.csv)",
    )
    us_bench_p.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON report instead of the table",
    )

    us_dry_run_p = sub.add_parser(
        "us-dry-run",
        help="run the U.S. federal dry-run kernel for one edition window",
        description=(
            "Derive the window's public laws from the before/after USC edition "
            "witness delta and run the dry-run section-replay kernel directly "
            "from the U.S. farchive. Prints the report summary. Thin shim over "
            "lawvm.us_federal.bench.derive_window_law_locators + "
            "lawvm.us_federal.dry_run.build_us_dry_run_from_archive."
        ),
    )
    us_dry_run_p.add_argument(
        "--title", type=int, required=True, help="USC title number (e.g. 11)"
    )
    us_dry_run_p.add_argument(
        "--before", type=int, required=True, dest="before_year",
        help="before-edition year (YYYY)",
    )
    us_dry_run_p.add_argument(
        "--after", type=int, required=True, dest="after_year",
        help="after-edition year (YYYY)",
    )
    us_dry_run_p.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON report instead of the summary",
    )

    us_source_p = sub.add_parser(
        "us-source",
        help="dump a parsed U.S. Code section (or title summary) from the farchive",
        description=(
            "Parse one USC annual-edition title from the U.S. farchive and dump "
            "the address, heading, and statutory text for one section (or a "
            "title-level section summary when --section is omitted). Thin shim "
            "over lawvm.us_federal.source_tree."
        ),
    )
    us_source_p.add_argument(
        "--title", type=int, required=True, help="USC title number (e.g. 11)"
    )
    us_source_p.add_argument(
        "--year", type=int, required=True, help="USC edition year (YYYY)"
    )
    us_source_p.add_argument(
        "--section",
        default=None,
        metavar="S",
        help="section number to dump (default: title-level section summary)",
    )
    us_source_p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human text",
    )

    us_spec_ledger_p = sub.add_parser(
        "us-spec-ledger",
        help="build the U.S. federal witness-attribution spec-discovery ledger",
        description=(
            "Build the per-rule discovered-spec ledger for U.S. federal: rank every "
            "us_* witness rule by how often the published USC after-edition oracle "
            "corroborates vs contradicts its believed_spec, over the dry-run bench "
            "corpus. Thin shim over lawvm.us_federal.spec_ledger_adapter; read-only, "
            "never authorizes replay."
        ),
    )
    us_spec_ledger_p.add_argument(
        "--corpus",
        metavar="PATH",
        default=None,
        help="bench corpus CSV (default: us/bench/us_bench_corpus.csv)",
    )
    us_spec_ledger_p.add_argument(
        "--json", action="store_true", help="emit the ledger JSON instead of the table"
    )
    us_spec_ledger_p.add_argument(
        "--json-out", default="", metavar="PATH", help="also write the ledger JSON here"
    )

    us_evidence_pack_p = sub.add_parser(
        "us-evidence-pack",
        help="export U.S. federal dry-run residuals as auditable evidence-pack JSONL",
        description=(
            "Project the U.S. federal dry-run kernel's per-section residuals "
            "(lawvm_wrong / oracle_suspect / missing_source / sunset_reversion), "
            "agreements, and typed refusals into one report-query-compatible "
            "evidence-row stream. Each residual becomes a sampleable row carrying "
            "the offending text, disposition, rule_id, the pinned USC section "
            "address, and the window. Read-only over the U.S. farchive; makes no "
            "replay claim. Thin shim over lawvm.us_federal.evidence_pack."
        ),
    )
    us_evidence_pack_p.add_argument(
        "--bench",
        action="store_true",
        help="export the full bench corpus (every evaluated window) instead of one window",
    )
    us_evidence_pack_p.add_argument(
        "--corpus",
        metavar="PATH",
        default=None,
        help="bench corpus CSV for --bench (default: us/bench/us_bench_corpus.csv)",
    )
    us_evidence_pack_p.add_argument(
        "--title",
        type=int,
        default=None,
        help="USC title number (single-window mode), or scope --bench to one title",
    )
    us_evidence_pack_p.add_argument(
        "--before", type=int, default=None, dest="before_year",
        help="before-edition year (YYYY) for single-window mode",
    )
    us_evidence_pack_p.add_argument(
        "--after", type=int, default=None, dest="after_year",
        help="after-edition year (YYYY) for single-window mode",
    )
    us_evidence_pack_p.add_argument(
        "--row-kind", default="", help="filter rows by kind (operation|finding)"
    )
    us_evidence_pack_p.add_argument(
        "--disposition",
        default="",
        help="filter rows by disposition (lawvm_wrong|oracle_suspect|missing_source|sunset_reversion|agreement)",
    )
    us_evidence_pack_p.add_argument(
        "--rule-id", default="", help="filter rows by witness rule id"
    )
    us_evidence_pack_p.add_argument(
        "--limit", type=int, default=40, metavar="N", help="rows to include in JSON output"
    )
    us_evidence_pack_p.add_argument(
        "--output-jsonl", metavar="PATH", help="write the evidence rows as report-query JSONL"
    )
    us_evidence_pack_p.add_argument(
        "--json", action="store_true", help="emit the evidence-pack report JSON instead of a summary line"
    )
    # --- END us_federal jurisdiction tooling ---

    # --- recipes ---
    sub.add_parser(
        "recipes",
        help="task-shaped workflow recipes (find/read/trace patterns with real command examples)",
        description=(
            "Print a curated set of task-shaped recipes mapping common research "
            "questions to the lawvm commands that serve them, with runnable examples.  "
            "Every command named in the recipe table is CI-verified against the live "
            "parser, so names stay accurate."
        ),
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


def _main_impl() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    # export-transition-graph and certificate-bundle deliberately take the
    # canonical säädös id in 'num/year' form (e.g. 301/2004); their handlers
    # normalize both orderings, so the year/num-only pre-1734 guard must not
    # reject them.
    if args.command not in (
        "export-transition-graph",
        "export-markdown-git",
        "certificate-bundle",
        "pack-work",
    ):
        _reject_pre_1734_fi_command_line_ids(args)

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
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm inspect-amendment does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.inspect_amendment import main as inspect_amendment_main

        inspect_amendment_main(args)

    elif args.command == "diagnose-phase":
        from lawvm.tools.diagnose_phase import main as diagnose_phase_main

        diagnose_phase_main(args)

    elif args.command == "invariant-bisect":
        from lawvm.tools.invariant_bisect import main as invariant_bisect_main

        invariant_bisect_main(args)

    elif args.command == "self-consistency":
        from lawvm.tools.self_consistency import main as self_consistency_main

        self_consistency_main(args)

    elif args.command == "snapshot-debug":
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm snapshot-debug does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.snapshot_debug import main as snapshot_debug_main

        snapshot_debug_main(args)

    elif args.command == "product-debug":
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm product-debug does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
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
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm trace-section does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
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
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            from lawvm.tools.uk_oracle_check import main as uk_classify_main

            uk_classify_main(args)
        elif j == "fi":
            from lawvm.tools.classify import main as classify_main

            classify_main(args)
        else:
            print(f"ERROR: lawvm classify does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

    elif args.command == "bench":
        j = getattr(args, "jurisdiction", "fi")
        _reject_uk_replay_regime_flags_for_non_uk(args, command="bench")
        if j == "ee":
            from lawvm.tools.ee_bench import main as ee_bench_main

            ee_bench_main(args)
        elif j == "uk":
            from lawvm.tools.uk_bench import main as uk_bench_main

            uk_bench_main(args)
        elif j == "nz":
            from lawvm.tools.nz_bench import main as nz_bench_main

            nz_bench_main(args)
        elif j == "us":
            # The US dry-run bench is fully implemented as a standalone entry
            # point (``python -m lawvm.us_federal.bench``). Reuse it verbatim by
            # translating the unified-CLI Namespace into its argv. It honours a
            # subset of the FI flags — corpus / parallel / json — and uses its
            # own default corpus (us/bench/us_bench_corpus.csv) when --corpus is
            # omitted. FI-only replay flags are not applicable and are ignored.
            from lawvm.us_federal.bench import main as us_bench_main

            us_argv: list[str] = []
            if getattr(args, "corpus", None):
                us_argv += ["--corpus", str(args.corpus)]
            us_parallel = getattr(args, "parallel", None)
            if us_parallel:
                us_argv += ["--parallel", str(us_parallel)]
            us_output_json = getattr(args, "output_json", None)
            if getattr(args, "json", False) or us_output_json:
                us_argv.append("--json")
            if us_output_json:
                import contextlib
                import io
                from pathlib import Path as _Path

                _buf = io.StringIO()
                with contextlib.redirect_stdout(_buf):
                    _rc = us_bench_main(us_argv)
                _Path(us_output_json).write_text(_buf.getvalue())
            else:
                _rc = us_bench_main(us_argv)
            raise SystemExit(_rc)
        elif j == "fi":
            from lawvm.tools.bench import main as bench_main

            bench_main(args)
        else:
            print(f"ERROR: lawvm bench does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

    elif args.command == "blame":
        j = getattr(args, "jurisdiction", "fi")
        if j == "ee":
            from lawvm.tools.ee_blame import main as ee_blame_main

            ee_blame_main(args)
        elif j == "fi":
            from lawvm.tools.blame import main as blame_main

            blame_main(args)
        else:
            print(f"ERROR: lawvm blame does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

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
            from lawvm.finland.replay_entrypoint import replay_xml
            from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml

            as_of = getattr(args, "as_of", "")
            verbose = getattr(args, "verbose", False)
            show_text = getattr(args, "show_text", False)
            use_json = getattr(args, "json", False)
            replay_meta: dict[str, object] = {}
            result = call_replay_xml(
                replay_xml,
                request=ReplayXmlRequest(
                    parent_id=args.base_id,
                    mode="legal_pit",
                    as_of=as_of,
                    quiet=not verbose,
                ),
                sinks=ReplayXmlSinks(replay_meta_out=replay_meta),
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
        elif args.nz_corpus_command == "frontier":
            from lawvm.new_zealand.frontier_work_items import main as nz_corpus_frontier_main

            nz_corpus_frontier_main(args)
        elif args.nz_corpus_command == "effect-candidates":
            from lawvm.new_zealand.effect_candidates import main as nz_corpus_effect_candidates_main

            nz_corpus_effect_candidates_main(args)
        elif args.nz_corpus_command == "candidate-preflight":
            from lawvm.new_zealand.effect_candidates import preflight_main as nz_corpus_candidate_preflight_main

            nz_corpus_candidate_preflight_main(args)
        elif args.nz_corpus_command == "dry-run":
            from lawvm.new_zealand.dry_run import main as nz_corpus_dry_run_main

            nz_corpus_dry_run_main(args)
        elif args.nz_corpus_command == "dry-run-oracle":
            from lawvm.new_zealand.dry_run_oracle import main as nz_corpus_dry_run_oracle_main

            nz_corpus_dry_run_oracle_main(args)
        elif args.nz_corpus_command == "replay-actual":
            from lawvm.new_zealand.actual_replay import main as nz_corpus_replay_actual_main

            nz_corpus_replay_actual_main(args)
        elif args.nz_corpus_command == "replay-chain":
            from lawvm.new_zealand.chain_replay import main as nz_corpus_replay_chain_main

            nz_corpus_replay_chain_main(args)
        elif args.nz_corpus_command == "replay-chain-corpus":
            from lawvm.new_zealand.chain_replay_corpus import (
                main as nz_corpus_replay_chain_corpus_main,
            )

            nz_corpus_replay_chain_corpus_main(args)
        elif args.nz_corpus_command == "build-corpus":
            from lawvm.new_zealand.bench_corpus import main as nz_corpus_build_corpus_main

            nz_corpus_build_corpus_main(args)
        elif args.nz_corpus_command == "dry-run-corpus":
            from lawvm.new_zealand.dry_run_corpus import main as nz_corpus_dry_run_corpus_main

            nz_corpus_dry_run_corpus_main(args)
        elif args.nz_corpus_command == "spec-ledger":
            from lawvm.new_zealand.spec_ledger_adapter import main as nz_corpus_spec_ledger_main

            nz_corpus_spec_ledger_main(args)
        elif args.nz_corpus_command == "dry-run-north-star":
            from lawvm.new_zealand.dry_run_north_star import main as nz_corpus_dry_run_north_star_main

            nz_corpus_dry_run_north_star_main(args)
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
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            from lawvm.tools.uk_structural_review import dump_uk_statute
            from pathlib import Path as _Path

            _db_arg = getattr(args, "db", None)
            _db_path = _Path(_db_arg) if _db_arg else None
            _sid = getattr(args, "statute_id", "")
            print(dump_uk_statute(_sid, compact=True, db_path=_db_path), end="")
        elif j == "fi":
            from lawvm.tools.diff import main as diff_main

            diff_main(args)
        else:
            print(f"ERROR: lawvm diff does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

    elif args.command == "ops":
        from lawvm.tools.ops import main as ops_main

        ops_main(args)

    elif args.command == "replay-debug":
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm replay-debug does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.replay_debug import main as replay_debug_main

        replay_debug_main(args)

    elif args.command == "replay-inspect":
        j = getattr(args, "jurisdiction", "fi")
        if j != "fi":
            print(f"ERROR: lawvm replay-inspect does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.replay_inspect import main as replay_inspect_main

        replay_inspect_main(args)

    elif args.command == "oracle-check":
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            from lawvm.tools.uk_oracle_check import main as uk_oracle_check_main

            uk_oracle_check_main(args)
        elif j == "fi":
            from lawvm.tools.oracle_check import main as oracle_check_main

            oracle_check_main(args)
        else:
            print(f"ERROR: lawvm oracle-check does not yet support -j {j}", file=sys.stderr)
            raise SystemExit(2)

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

    elif args.command == "provision-state":
        from lawvm.tools.provision_state import main as provision_state_main

        provision_state_main(args)

    elif args.command == "read":
        from lawvm.tools.read_provision import main as read_main

        read_main(args)

    elif args.command == "reconcile":
        if getattr(args, "sweep", False):
            from lawvm.tools.reconcile_sweep import main as reconcile_sweep_main

            reconcile_sweep_main(args)
        else:
            if not getattr(args, "statute_id", ""):
                print(
                    "reconcile: a statute_id is required (or use --sweep)",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            from lawvm.tools.reconcile import main as reconcile_main

            reconcile_main(args)

    elif args.command in ("provenance", "trace"):
        from lawvm.tools.provenance import main as provenance_main

        provenance_main(args)

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
            json_output=getattr(args, "json", False),
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

    elif args.command == "uk-acquire":
        from lawvm.tools.uk_acquire import main as uk_acquire_main

        uk_acquire_main(args)

    elif args.command == "uk-corpus":
        from lawvm.tools.uk_corpus import main as uk_corpus_main

        uk_corpus_main(args)

    elif args.command == "uk-effect":
        from lawvm.tools.uk_effect import main as uk_effect_main

        uk_effect_main(args)

    elif args.command == "uk-effects":
        from lawvm.tools.uk_effects import main as uk_effects_main

        uk_effects_main(args)

    elif args.command == "uk-cross-statute-graph":
        from lawvm.tools.uk_cross_statute_graph import main as uk_cross_statute_graph_main

        uk_cross_statute_graph_main(args)

    elif args.command == "uk-eids":
        from lawvm.tools.uk_eids import main as uk_eids_main

        uk_eids_main(args)

    elif args.command == "uk-misses":
        from lawvm.tools.uk_misses import main as uk_misses_main

        uk_misses_main(args)

    elif args.command == "uk-candidates":
        from lawvm.tools.uk_candidates import main as uk_candidates_main

        uk_candidates_main(args)

    elif args.command == "uk-manual-frontier-validate":
        from lawvm.tools.uk_manual_frontier import main as uk_manual_frontier_main

        uk_manual_frontier_main(args)

    elif args.command == "uk-semantic-claims-validate":
        from lawvm.tools.uk_semantic_claims import main as uk_semantic_claims_main

        uk_semantic_claims_main(args)

    elif args.command == "uk-live-target-index":
        from lawvm.tools.uk_live_targets import main as uk_live_target_index_main

        uk_live_target_index_main(args)

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

    elif args.command == "bench-triage":
        from lawvm.tools.bench_triage import main as bench_triage_main

        bench_triage_main(args)

    elif args.command == "strict-report":
        from lawvm.tools.strict_report import main as strict_report_main

        strict_report_main(args)

    elif args.command == "capture":
        from lawvm.tools.capture import main as capture_main

        capture_main(args)

    elif args.command == "audit-trail":
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            print("ERROR: lawvm audit-trail does not yet support -j uk", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.audit_trail import main as audit_trail_main

        audit_trail_main(args)

    elif args.command == "lower-audit":
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            print("ERROR: lawvm lower-audit does not yet support -j uk", file=sys.stderr)
            raise SystemExit(2)
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
        j = getattr(args, "jurisdiction", "fi")
        if j == "uk":
            print("ERROR: lawvm step-attribution does not yet support -j uk", file=sys.stderr)
            raise SystemExit(2)
        from lawvm.tools.step_attribution import main as sa_main

        sa_main(args)

    elif args.command == "sweden":
        from lawvm.tools.sweden import main as sweden_main

        sweden_main(args)

    elif args.command == "finland-rulebook":
        from lawvm.tools.finland_rulebook import main as finland_rulebook_main

        finland_rulebook_main(args)

    elif args.command == "fi-periodic-table":
        from lawvm.tools.fi_periodic_table import main as fi_periodic_table_main

        fi_periodic_table_main(args)

    elif args.command == "fi-timeline-robust-sweep":
        from lawvm.tools.fi_timeline_robust_sweep import main as fi_timeline_robust_sweep_main

        fi_timeline_robust_sweep_main(args)

    elif args.command == "drift":
        from lawvm.tools.drift import main as drift_main

        drift_main(args)

    elif args.command == "sync-finlex":
        from pathlib import Path as _Path
        from farchive import Farchive as _FA
        from lawvm.corpus_store import validate_farchive_create_path as _validate_farchive_create_path
        from lawvm.finland.finlex_api import sync_changes as _sync_changes

        _default_db_sf = _Path("data/finlex.farchive")
        _db_path = _Path(args.db) if getattr(args, "db", None) else _default_db_sf

        _dry = getattr(args, "dry_run", False) or getattr(args, "list_only", False)
        if _dry:
            _stats = _sync_changes(
                archive=None,
                since=args.since,
                delay=args.delay,
                lang=args.lang,
                doc_type=args.doc_type,
                dry_run=True,
                verbose=getattr(args, "verbose", False),
            )
            print(
                f"fetched={_stats['fetched']}  modified={_stats['modified']}  "
                f"added={_stats['added']}  deleted={_stats['deleted']}  "
                f"skipped={_stats['skipped']}  errors={_stats['errors']}"
            )
            if _stats["errors"]:
                sys.exit(1)
            return

        if not _db_path.exists():
            _validate_farchive_create_path(_db_path)
        _db_path.parent.mkdir(parents=True, exist_ok=True)
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

    elif args.command == "acquire-fi-proposals":
        from lawvm.finland.he_acquisition import main as acquire_he_main

        acquire_he_main(args)

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
            _jur = str(getattr(args, "jurisdiction", "fi") or "fi")
            if _jur == "uk":
                from lawvm.tools.uk_structural_review import dump_uk_statute
                from pathlib import Path as _Path
                if not args.statute_id:
                    print("ERROR: statute_id required for -j uk --dump", file=sys.stderr)
                    sys.exit(1)
                _db_arg = getattr(args, "db", None)
                _db_path = _Path(_db_arg) if _db_arg else None
                result = dump_uk_statute(
                    args.statute_id,
                    compact=getattr(args, "compact", False),
                    section_filter=getattr(args, "section", None),
                    db_path=_db_path,
                )
                sys.stdout.write(result)
            elif getattr(args, "triple", False):
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

    elif args.command == "branch-demo":
        from lawvm.tools.branch_demo import main as branch_demo_main

        branch_demo_main(args)

    elif args.command == "uk-branch-demo":
        from lawvm.tools.uk_branch_demo import main as uk_branch_demo_main

        uk_branch_demo_main(args)

    elif args.command == "uk-branch-import":
        from lawvm.tools.uk_branch_import import main as uk_branch_import_main

        uk_branch_import_main(args)

    elif args.command == "sql":
        from lawvm.tools.sql_query import main as sql_main

        sql_main(args)

    elif args.command == "refs":
        from lawvm.tools.refs_query import main as refs_main

        refs_main(args)

    elif args.command == "preparatory-refs":
        from lawvm.tools.preparatory_refs_query import main as prep_refs_main

        prep_refs_main(args)

    elif args.command == "inline-citations":
        from lawvm.tools.inline_citations_query import main as inline_citations_main

        inline_citations_main(args)

    elif args.command == "actors":
        from lawvm.tools.actors_query import main as actors_main

        actors_main(args)

    elif args.command == "pools":
        from lawvm.tools.pools_query import main as pools_main

        pools_main(args)

    elif args.command == "fi-proposals":
        from lawvm.tools.fi_proposals_query import main as fi_proposals_main

        fi_proposals_main(args)

    elif args.command == "fi-proposal-show":
        from lawvm.tools.fi_proposal_show import main as fi_proposal_show_main

        fi_proposal_show_main(args)

    elif args.command == "fi-proposal-bundle":
        from lawvm.tools.fi_proposal_bundle import main as fi_proposal_bundle_main

        fi_proposal_bundle_main(args)

    elif args.command == "fi-proposal-history":
        from lawvm.tools.fi_proposal_history import main as fi_proposal_history_main

        fi_proposal_history_main(args)

    elif args.command == "fi-proposals-competing":
        from lawvm.tools.fi_proposals_competing import main as fi_proposals_competing_main

        fi_proposals_competing_main(args)

    elif args.command == "sync-fi-proposals":
        from lawvm.tools.sync_fi_proposals import main as sync_fi_proposals_main

        sync_fi_proposals_main(args)

    elif args.command == "bench-report":
        from lawvm.tools.bench_report import main as bench_report_main

        bench_report_main(args)

    elif args.command == "rebuild-indexes":
        from lawvm.tools.rebuild_indexes import main as rebuild_indexes_main

        rebuild_indexes_main(args)

    elif args.command == "build-index-db":
        from lawvm.tools.build_index_db import main as build_index_db_main

        build_index_db_main(args)

    elif args.command == "parse-johto":
        from lawvm.tools.parse_johto import main as parse_johto_main

        parse_johto_main(args)

    elif args.command == "fi-parse-explain":
        from lawvm.tools.fi_parse_explain import main as fi_parse_explain_main

        fi_parse_explain_main(args)

    elif args.command == "fi-refs":
        from lawvm.tools.fi_refs_view import main as fi_refs_view_main

        fi_refs_view_main(args)

    elif args.command == "fi-parse":
        from lawvm.tools.fi_parse_view import main as fi_parse_view_main

        fi_parse_view_main(args)

    elif args.command == "analyze-bill":
        from lawvm.tools.bill_analysis import main as analyze_bill_main

        analyze_bill_main(args)

    elif args.command == "bill-counterfactual":
        from lawvm.tools.bill_counterfactual_effects import (
            main as bill_counterfactual_main,
        )

        bill_counterfactual_main(args)

    elif args.command == "parse-bench":
        from lawvm.tools.parse_bench import main as parse_bench_main

        parse_bench_main(args)

    elif args.command == "refs-bench":
        from lawvm.tools.refs_bench import main as refs_bench_main

        refs_bench_main(args)

    elif args.command == "surface-lints":
        from lawvm.tools.surface_lints import main as surface_lints_main

        surface_lints_main(args)

    elif args.command == "broken-refs":
        from lawvm.tools.bitemporal_refs import main as broken_refs_main

        broken_refs_main(args)

    elif args.command == "dangling-refs":
        from lawvm.tools.dangling_references import main as dangling_refs_main

        dangling_refs_main(args)

    elif args.command == "cross-ref-report":
        from lawvm.tools.cross_reference_integrity_report import (
            main as cross_ref_report_main,
        )

        cross_ref_report_main(args)

    elif args.command == "surface-graph":
        from lawvm.tools.surface_graph import main as surface_graph_main

        surface_graph_main(args)

    elif args.command == "corpus-graph":
        from lawvm.tools.corpus_surface_graph import main as corpus_graph_main

        corpus_graph_main(args)

    elif args.command == "parse-characterize":
        from lawvm.tools.parse_characterize import main as parse_characterize_main

        parse_characterize_main(args)

    elif args.command == "build-statute-name-registry":
        from lawvm.tools.build_statute_name_registry import (
            main as build_statute_name_registry_main,
        )

        build_statute_name_registry_main(args)

    elif args.command == "fi-source-label-audit":
        from lawvm.tools.fi_source_label_audit import main as fi_source_label_audit_main

        fi_source_label_audit_main(args)

    elif args.command == "topic":
        from lawvm.tools.cmd_topic import main as topic_main

        topic_main(args)

    elif args.command == "follow-refs":
        from lawvm.tools.cmd_follow_refs import main as follow_refs_main

        follow_refs_main(args)

    elif args.command == "pit-timeline":
        from lawvm.tools.cmd_pit_timeline import main as pit_timeline_main

        pit_timeline_main(args)

    elif args.command == "pit-diff":
        from lawvm.tools.cmd_pit_diff import main as pit_diff_main

        pit_diff_main(args)

    elif args.command == "telos":
        from lawvm.tools.cmd_telos import main as telos_main

        telos_main(args)

    elif args.command == "simulate":
        from lawvm.tools.simulate import main as simulate_main

        simulate_main(args)

    elif args.command == "export-fi-he-branch-ops":
        from lawvm.tools.export_fi_he_branch_ops import main as efbo_main

        efbo_main(args)

    elif args.command == "claim":
        # Activate Finland claim kinds (registers fi.v1.* into core registry)
        importlib.import_module("lawvm.finland.claim_kinds")
        from lawvm.tools.cmd_claim import main as claim_main

        claim_main(args)

    elif args.command == "propose-claims":
        importlib.import_module("lawvm.finland.claim_kinds")
        from lawvm.tools.cmd_propose_claims import main as propose_main

        propose_main(args)

    elif args.command == "validate-claims":
        importlib.import_module("lawvm.finland.claim_kinds")
        from lawvm.tools.cmd_validate_claims import main as validate_claims_main

        validate_claims_main(args)

    elif args.command == "recipes":
        from lawvm.tools.cmd_recipes import main as recipes_main

        recipes_main(args)

    elif args.command == "export-transition-graph":
        from lawvm.tools.export_transition_graph import main as export_transition_graph_main

        export_transition_graph_main(args)

    elif args.command == "export-markdown-git":
        from lawvm.tools.export_markdown_git import main as export_markdown_git_main

        export_markdown_git_main(args)

    elif args.command == "certificate-bundle":
        from lawvm.tools.certificate_bundle import main as certificate_bundle_main

        certificate_bundle_main(args)

    # --- BEGIN substrate pack dispatch (additive, self-contained) ---
    elif args.command == "pack-work":
        from lawvm.substrate.exporter import export_work_pack

        _juris = str(getattr(args, "jurisdiction", "fi") or "fi")
        if _juris == "fi":
            # Strict year-major gate at the CLI boundary: reject the Finnish
            # num/year citation form (e.g. 301/2004) rather than silently
            # swapping it. See lawvm.finland.statute_id.require_year_major.
            from lawvm.finland.statute_id import require_year_major

            require_year_major(args.work_id)
        _result = export_work_pack(
            args.work_id,
            args.out,
            jurisdiction=_juris,
            slice_prefix=getattr(args, "slice", "") or "",
            granularity=getattr(args, "granularity", "subsection") or "subsection",
            quiet=False,
        )
        print("", flush=True)
        print(f"  work_id:          {_result.work_id}", flush=True)
        print(f"  out dir:          {_result.out_dir}", flush=True)
        print(f"  pack_id:          {_result.pack_id}", flush=True)
        print(f"  change_dates:     {_result.n_change_dates}", flush=True)
        print(
            f"  content_leaves:   {_result.n_content_leaves} "
            f"(of {_result.leaf_dedup_attempts} stored attempts; "
            f"dedup ratio "
            f"{1 - _result.n_content_leaves / max(1, _result.leaf_dedup_attempts):.1%})",
            flush=True,
        )
        print(f"  node_versions:    {_result.n_node_versions}", flush=True)
        print(f"  selection_rows:   {_result.n_selection_rows}", flush=True)
        print(f"  address_nodes:    {_result.n_address_nodes}", flush=True)
        print(f"  transitions:      {_result.n_transitions}", flush=True)
        print(f"  checkpoints:      {_result.n_checkpoints}", flush=True)
        print(f"  residuals:        {_result.n_residuals}", flush=True)

    elif args.command == "pack-snapshot":
        if str(getattr(args, "source", "")) != "locus":
            parser.error("pack-snapshot only supports --source locus in v0")
        from lawvm.substrate.locus import WorkKey, export_snapshot_pack

        _state, _, _locality = str(args.work).partition("/")
        if not _state or not _locality:
            parser.error("--work must be 'STATE/LOCALITY' (e.g. ak/kingcove)")
        _jtype = str(getattr(args, "jurisdiction_type", "cities"))
        _key = (
            WorkKey(state=_state, city=_locality, county=None, jurisdiction_type=_jtype)
            if _jtype == "cities"
            else WorkKey(state=_state, city=None, county=_locality, jurisdiction_type=_jtype)
        )
        if not str(args.data_glob):
            parser.error(
                "--data is required (a LOCUS parquet glob), or set LAWVM_LOCUS_DATA_GLOB"
            )
        print(f"[pack-snapshot] reading LOCUS work {_state}/{_locality} ({_jtype})...", flush=True)
        _snap = export_snapshot_pack(
            str(args.data_glob),
            _key,
            args.out,
            emit_overlay=not bool(getattr(args, "no_overlay", False)),
        )
        print("", flush=True)
        print(f"  work_id:          {_snap.work_id}", flush=True)
        print(f"  out dir:          {_snap.out_dir}", flush=True)
        print(f"  pack_id:          {_snap.pack_id}", flush=True)
        print(f"  source rows:      {_snap.n_rows}", flush=True)
        print(f"  addressable:      {_snap.n_addressable_leaves} leaves", flush=True)
        print(f"  address_nodes:    {_snap.n_address_nodes}", flush=True)
        print(f"  content_leaves:   {_snap.n_content_leaves}", flush=True)
        print(f"  selection_rows:   {_snap.n_selection_rows}", flush=True)
        print(f"  overlay_rows:     {_snap.n_overlay_rows}", flush=True)
        print(
            f"  residuals:        {_snap.n_residuals} typed "
            f"({', '.join(_snap.residual_kinds) or 'none'}); "
            f"header-parse residue {_snap.header_parse_residuals}/{_snap.n_rows}",
            flush=True,
        )
        _mc = ", ".join(
            f"{_m}={_c}" for _m, _c in sorted(_snap.method_counts.items()) if _c
        )
        print(f"  induction:        {_mc or 'none'}", flush=True)

    elif args.command == "check-pack":
        from lawvm.substrate.checker import CheckMode, IntegrityVerdict, check_pack
        from lawvm.substrate.exporter import load_pack_for_check

        # Route snapshot packs (pack_kind lawvm.pack.snapshot.*) through the
        # snapshot reader (extended known-schema set for source-lineage rows);
        # everything else uses the FI exporter reader. The two readers share the
        # substrate Pack shape, so the checker is the same downstream.
        import json as _json
        from pathlib import Path as _Path

        _mf = _json.loads(
            (_Path(args.pack_dir) / "manifest.json").read_text(encoding="utf-8")
        )
        _mf_body = _mf.get("object", _mf)
        if str(_mf_body.get("pack_kind", "")).startswith("lawvm.pack.snapshot"):
            from lawvm.substrate.locus import load_snapshot_pack_for_check

            _pack = load_snapshot_pack_for_check(args.pack_dir)
        else:
            _pack = load_pack_for_check(args.pack_dir)
        _mode = (
            CheckMode.AUDIT
            if str(getattr(args, "mode", "browse")) == "audit"
            else CheckMode.BROWSE
        )
        _verdict = check_pack(_pack, mode=_mode)
        print(f"top_line_verdict: {_verdict.top_line_verdict.value}", flush=True)
        print(f"integrity:        {_verdict.integrity.value}", flush=True)
        print(f"certification:    {_verdict.certification.value}", flush=True)
        _tot = _verdict.totality
        print(f"totality:         {_tot.verdict.value}", flush=True)
        print(
            f"  universe:       {_tot.owned_nodes} selected + "
            f"{_tot.typed_non_selection_nodes} typed-reason / "
            f"{_tot.addressable_nodes} addressable nodes",
            flush=True,
        )
        if _tot.residual_count:
            print(
                f"  residuals:      {_tot.residual_count} typed "
                f"({', '.join(_tot.residual_kinds) or 'untyped'})",
                flush=True,
            )
        if _tot.coverage_classes:
            print(f"  coverage:       {', '.join(_tot.coverage_classes)}", flush=True)
        if _tot.shortfalls:
            print(f"  shortfalls:     {len(_tot.shortfalls)}", flush=True)
            for _s in _tot.shortfalls[:20]:
                print(f"    - [{_s.code.value}] {_s.subject}: {_s.detail}", flush=True)
        print(f"checked_levels:   {', '.join(_verdict.checked_levels)}", flush=True)
        if _verdict.violations:
            print(f"violations:       {len(_verdict.violations)}", flush=True)
            for _v in _verdict.violations[:20]:
                print(f"  - [{_v.code.value}] {_v.layer}/{_v.subject}: {_v.detail}", flush=True)
        else:
            print("violations:       0", flush=True)
        _clean = _verdict.integrity in (
            IntegrityVerdict.VALID,
            IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS,
        )
        raise SystemExit(0 if _clean else 1)

    elif args.command == "pack-corpus":
        from lawvm.substrate.corpus import build_corpus_pack, measure_leaf_dedup

        _members = {str(p): p for p in args.member_packs}
        if len(_members) < 2:
            print("pack-corpus needs >=2 distinct member pack directories", flush=True)
            raise SystemExit(2)
        _report = measure_leaf_dedup(_members)
        print("cross-work content-leaf dedup:", flush=True)
        print(f"  {_report.summary()}", flush=True)
        if getattr(args, "measure_only", False):
            raise SystemExit(0)
        _cresult = build_corpus_pack(
            member_pack_dirs=_members,
            out_dir=args.out,
            resolutions=[],
        )
        print("", flush=True)
        print(f"  corpus pack_id:   {_cresult.pack_id}", flush=True)
        print(f"  out dir:          {_cresult.out_dir}", flush=True)
        print(f"  work_ids:         {list(_cresult.work_ids)}", flush=True)
        print(f"  shared base leaves: {_cresult.n_shared_base_leaves}", flush=True)
        print(f"  edges:            {_cresult.n_edges}", flush=True)
    # --- END substrate pack dispatch ---

    # --- BEGIN us_federal jurisdiction dispatch (additive, self-contained) ---
    elif args.command == "us-import-plaw":
        from pathlib import Path as _Path

        from lawvm.us_federal.import_plaw import import_plaw_sources

        _dest = _Path(args.dest) if getattr(args, "dest", None) else None
        report = import_plaw_sources(
            list(args.sources),
            db_path=_dest,
            skip_existing=bool(getattr(args, "skip_existing", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        print(
            f"PLAW import: scanned={report.total_scanned:,} "
            f"imported={report.total_imported:,} "
            f"skipped={report.total_skipped:,} errors={report.total_errors:,}"
        )
        if report.total_errors:
            sys.exit(1)

    elif args.command == "us-import-usc":
        from pathlib import Path as _Path

        from lawvm.us_federal.import_usc import import_usc_sources

        _dest = _Path(args.dest) if getattr(args, "dest", None) else None
        report = import_usc_sources(
            [(src, None) for src in args.sources],
            db_path=_dest,
            skip_existing=bool(getattr(args, "skip_existing", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        print(
            f"USC import: scanned={report.total_scanned:,} "
            f"imported={report.total_imported:,} "
            f"skipped={report.total_skipped:,} errors={report.total_errors:,}"
        )
        if report.total_errors:
            sys.exit(1)

    elif args.command == "us-inventory":
        import json
        from pathlib import Path as _Path

        from lawvm.us_federal.inventory import inventory_us_federal

        _dest = _Path(args.dest) if getattr(args, "dest", None) else None
        inv = inventory_us_federal(
            db_path=_dest, congress=getattr(args, "congress", None)
        )
        if getattr(args, "json", False):
            print(json.dumps(inv.to_dict(), indent=2, ensure_ascii=False))
        else:
            print("U.S. federal PLAW inventory (amendment-source units only):")
            print(f"  Total Public Laws: {inv.total_units:,}")
            for congress in inv.congresses:
                print(
                    f"    Congress {congress}: "
                    f"{inv.counts_per_congress[congress]:,}"
                )

    elif args.command == "us-bench":
        import json
        from pathlib import Path as _Path

        from lawvm.us_federal.bench import (
            DEFAULT_CORPUS_PATH,
            run_bench,
            _render_table,
            load_corpus,
        )
        from lawvm.us_federal.sources import open_us_federal_farchive

        _corpus = (
            _Path(args.corpus) if getattr(args, "corpus", None) else DEFAULT_CORPUS_PATH
        )
        if not _corpus.exists():
            print(f"error: bench corpus not found: {_corpus}", file=sys.stderr)
            sys.exit(1)
        _windows = load_corpus(_corpus)
        _archive = open_us_federal_farchive(readonly=True)
        try:
            _report = run_bench(_archive, _windows, corpus_path=str(_corpus))
        finally:
            _archive.close()
        if getattr(args, "json", False):
            print(json.dumps(_report.to_jsonable(), indent=2, sort_keys=True))
        else:
            print(_render_table(_report))
            _agg = _report.aggregate()
            _cov = _agg["coverage_fraction"]
            _cov_str = "-" if _cov is None else f"{_cov:.4f}"
            print()
            print(
                f"AGGREGATE  windows={_agg['windows_evaluated']} "
                f"(skipped {_agg['windows_skipped']})  "
                f"witness-anchored coverage={_agg['agreements_total']}/"
                f"{_agg['oracle_changed_section_total']} = {_cov_str}"
            )
            print(f"  disposition breakdown: {_agg['disposition_breakdown']}")
            print("  replay_authorized: False (dry-run gate)")

    elif args.command == "us-dry-run":
        import json

        from lawvm.us_federal.bench import derive_window_law_locators
        from lawvm.us_federal.dry_run import build_us_dry_run_from_archive
        from lawvm.us_federal.sources import open_us_federal_farchive

        _archive = open_us_federal_farchive(readonly=True)
        try:
            _locators = derive_window_law_locators(
                _archive,
                title=args.title,
                before_year=args.before_year,
                after_year=args.after_year,
            )
            if _locators is None:
                print(
                    "error: before/after USC edition missing from the U.S. "
                    f"farchive for title {args.title} "
                    f"({args.before_year}->{args.after_year})",
                    file=sys.stderr,
                )
                sys.exit(1)
            _report = build_us_dry_run_from_archive(
                _archive,
                title=args.title,
                before_year=args.before_year,
                after_year=args.after_year,
                plaw_locators=_locators,
            )
        finally:
            _archive.close()
        if getattr(args, "json", False):
            print(json.dumps(_report.to_jsonable(), indent=2, sort_keys=True))
        else:
            print(json.dumps(_report.summary(), indent=2, sort_keys=True))

    elif args.command == "us-source":
        import json

        from lawvm.us_federal.source_tree import parse_usc_title_document
        from lawvm.us_federal.sources import (
            open_us_federal_farchive,
            read_usc_annual,
            usc_annual_locator,
        )

        _archive = open_us_federal_farchive(readonly=True)
        try:
            _blob = read_usc_annual(_archive, args.year, args.title)
        finally:
            _archive.close()
        if _blob is None:
            print(
                "error: USC edition not in the U.S. farchive: "
                f"{usc_annual_locator(args.year, args.title)}",
                file=sys.stderr,
            )
            sys.exit(1)
        _doc = parse_usc_title_document(
            _blob,
            title=args.title,
            year=str(args.year),
            locator=usc_annual_locator(args.year, args.title),
        )
        _section = getattr(args, "section", None)
        if _section is None:
            if getattr(args, "json", False):
                print(json.dumps(_doc.to_jsonable(), indent=2, ensure_ascii=False))
            else:
                print(
                    f"USC title {_doc.title} ({_doc.year}) "
                    f"{_doc.locator}: {_doc.report.section_count} sections "
                    f"({_doc.report.repealed_count} repealed)"
                )
                for s in _doc.sections:
                    _flag = " [repealed]" if s.repealed else ""
                    print(f"  § {s.section}  {s.heading}{_flag}")
        else:
            _sec = _doc.section_by_number(_section)
            if _sec is None:
                print(
                    f"error: section {_section} not found in USC title "
                    f"{args.title} ({args.year})",
                    file=sys.stderr,
                )
                sys.exit(1)
            if getattr(args, "json", False):
                print(
                    json.dumps(
                        _sec.to_jsonable(include_paragraphs=True),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"address: {_sec.address}")
                print(f"heading: § {_sec.section}. {_sec.heading}")
                print(f"repealed: {_sec.repealed}")
                if _sec.chapter or _sec.subchapter:
                    print(
                        f"container: chapter {_sec.chapter or '-'} "
                        f"subchapter {_sec.subchapter or '-'}"
                    )
                print("statutory_text:")
                print(_sec.statutory_text)

    elif args.command == "us-spec-ledger":
        import json
        from pathlib import Path as _Path

        from lawvm.us_federal.bench import DEFAULT_CORPUS_PATH, load_corpus
        from lawvm.us_federal.sources import open_us_federal_farchive
        from lawvm.us_federal.spec_ledger_adapter import (
            build_us_spec_ledger,
            ledger_to_dict,
            render_text,
        )

        _corpus = (
            _Path(args.corpus) if getattr(args, "corpus", None) else DEFAULT_CORPUS_PATH
        )
        if not _corpus.exists():
            print(f"error: bench corpus not found: {_corpus}", file=sys.stderr)
            sys.exit(1)
        _windows = load_corpus(_corpus)
        _archive = open_us_federal_farchive(readonly=True)
        try:
            _ledger = build_us_spec_ledger(_archive, _windows)
        finally:
            _archive.close()
        if getattr(args, "json", False):
            print(json.dumps(ledger_to_dict(_ledger), ensure_ascii=False, indent=2))
        else:
            print(render_text(_ledger))
        _json_out = getattr(args, "json_out", "")
        if _json_out:
            with open(_json_out, "w", encoding="utf-8") as _fh:
                json.dump(ledger_to_dict(_ledger), _fh, ensure_ascii=False, indent=2)
            print(f"wrote {_json_out}", file=sys.stderr)

    elif args.command == "us-evidence-pack":
        from lawvm.us_federal.evidence_pack import main as us_evidence_pack_main

        us_evidence_pack_main(args)
    # --- END us_federal jurisdiction dispatch ---

    elif args.command is None:
        parser.print_help()
        sys.exit(1)

    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Run the LawVM CLI, treating closed stdout pipes as normal termination."""

    try:
        _main_impl()
    except BrokenPipeError:
        # Standard Unix pipelines such as ``lawvm strict-report --json | head``
        # close stdout early.  Do not turn that consumer choice into a Python
        # traceback; redirect final interpreter flushes away from the closed fd.
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass


if __name__ == "__main__":
    main()
