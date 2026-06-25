"""lawvm bench — corpus benchmark as first-class CLI with history.

Runs the full or partial benchmark, records results, and tracks score
trajectory over time. Detects regressions that individual score snapshots miss.

Usage:
    lawvm bench                              # run benchmark (all statutes)
    lawvm bench --label v22                  # tag this run
    lawvm bench --history                    # show score trajectory
    lawvm bench --regressions                # statutes worse than previous run
    lawvm bench --compare v17 v21            # diff two labeled runs
    lawvm bench --show v66                   # show worst performers from a past run
    lawvm bench --corpus .tmp/my_list.csv    # custom corpus list
    lawvm bench --top 20                     # report only worst 20

History is appended to LawVM/data/benchmark_history.csv.
Per-run per-statute results saved to LawVM/data/bench_runs/.
"""

from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import io
import json
import os
import re
import sys
import time
import warnings as py_warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, cast

if TYPE_CHECKING:
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

from rapidfuzz.distance import Indel as _RapidFuzzIndel

from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.consolidated_store import pin_selection_as_of
from lawvm.finland.corpus import get_ground_truth, get_ground_truth_bytes, get_ground_truth_tree
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_timeline_diagnostics import (
    fi_bench_timeline_invariants_enabled,
    fi_timeline_invariants_opt_in_enabled,
)
from lawvm.finland.proof_surfaces import finland_bench_run_evidence_surface
from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
from lawvm.finland.transparent_store import is_known_missing_source
from lawvm.finland.xml_statute import serialize_text as _serialize_ir_text
from lawvm.semantic.projection import get_oracle_text_normalizer
from lawvm.tools.editorial_hygiene import (  # via shim (pulls registration)
    count_kumottu_bytes,
    normalize_finlex_oracle_comparison_text,
    strip_editorial_annotations,
)
from lawvm.tools.frontier import _run_oracle_checks_parallel
from lawvm.tools.pit_projection import comparison_ir_for_pit
from lawvm.tools.uk_replay_regime import add_uk_replay_regime_arguments
from lawvm.tools.bench_diagnostic_tiers import (
    bench_diagnostic_sidecar_rows,
    enrich_bench_finding_counts,
    format_tiered_bench_warning_summary,
    merge_bench_diagnostic_counts,
    merge_diagnostic_counter_dicts,
    print_bench_diagnostic_tier_rollup,
)
from lawvm.tools.replay_mode_arg import replay_mode_argument

# ---------------------------------------------------------------------------
# Live-filter helper (skip contentAbsent-oracle statutes)
# ---------------------------------------------------------------------------

_CONTENT_ABSENT_BYTES = b"contentAbsent"


_REPEALED_THRESHOLD = 0.5  # fraction of <section> elements that are kumottu → skip
_EMPTY_MAX_SECTIONS = 3  # ≤N sections AND 0 kumottu AND small body → EMPTY
_EMPTY_MAX_BYTES = 2000  # body text (tag-stripped) shorter than this → EMPTY
_ORACLE_STALE_DIAGNOSIS = "ORACLE_STALE"
_FI_BENCH_DEFAULT_MAX_WORKERS = 16
# Non-scored statuses that are NOT crashes: the statute simply has no oracle to
# compare against (fully-superseded decree, missing source, oracle redirected to a
# different successor statute). These are excluded from scoring but must NOT be
# reported as "CRASHED" — only genuine exceptions (status == str(exception)) are.
_NONSCORED_STATUSES = frozenset({"NO_TRUTH", "SOURCE_UNAVAILABLE", _ORACLE_STALE_DIAGNOSIS})
_LATEST_CONSOLIDATED_SELECTOR = ConsolidatedArtifactSelector.latest_cached_editorial()
_BENCH_CONSOLIDATED_SELECTOR = ConsolidatedArtifactSelector.bench_comparable()


def _format_error_for_display(similarity: float, unit_status: str) -> str:
    if similarity >= 0:
        return f"{(1 - similarity) * 100:.2f}%"
    if unit_status in _NONSCORED_STATUSES:
        return "n/a"
    return "ERR"


def _format_similarity_for_csv(similarity: float, unit_status: str) -> str:
    if similarity >= 0:
        return f"{similarity:.6f}"
    if unit_status in _NONSCORED_STATUSES:
        return unit_status
    return "ERR"


def _format_bench_warning_summary(diagnostics: Counter[str]) -> str:
    return format_tiered_bench_warning_summary(diagnostics)


def _summarize_bench_replay_result_diagnostics(master: Any, captured_counts: Counter[str]) -> Counter[str]:
    """Merge captured warnings with typed replay evidence that bench otherwise drops."""
    return merge_bench_diagnostic_counts(captured_counts, enrich_bench_finding_counts(master))


def _merge_bench_structural_diagnostics(diagnostics: Counter[str], event_counts: Dict[str, int]) -> Counter[str]:
    """Attach structural-diff event families to the persisted bench diagnostics."""
    merged = Counter(diagnostics)
    for kind, count in event_counts.items():
        if count:
            merged[f"structural:{kind}"] += count
    return merged


def _summarize_bench_warning_diagnostics(
    stdout_text: str,
    stderr_text: str,
    caught_warnings: list[py_warnings.WarningMessage],
) -> Counter[str]:
    counts: Counter[str] = Counter()

    def _add(kind: str) -> None:
        counts[kind] += 1

    for text in (stdout_text, stderr_text):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" in line:
                _add("coverage_degraded")
            elif "WARNING tree invariant:" in line:
                _add("tree_invariant")
            elif "WARNING text duplication:" in line:
                _add("text_duplication")
            elif "WARNING source pathology:" in line:
                _add("source_pathology")
            elif "WARNING timeline invariant:" in line:
                _add("timeline_invariant")
            elif "WARNING product invariant:" in line:
                _add("product_invariant")
            elif "WARNING oracle suspect:" in line:
                _add("oracle_suspect")
            elif "WARNING" in line:
                _add("warning_other")

    for warning in caught_warnings:
        message = str(warning.message)
        if "empty same-day temporal interval" in message:
            _add("same_day_empty_interval")
        else:
            _add(warning.category.__name__.lower())

    return counts


def _bench_should_allocate_replay_meta(*, quiet: bool, diagnostic_replay: bool) -> bool:
    """Timeline invariant hook needs a live replay_meta_out dict to arm itself."""
    return (
        fi_timeline_invariants_opt_in_enabled()
        or fi_bench_timeline_invariants_enabled(diagnostic_replay=diagnostic_replay)
        or fi_bench_timeline_invariants_enabled(diagnostic_replay=not quiet)
    )


def _bench_replay_sinks(*, quiet: bool, diagnostic_replay: bool) -> ReplayXmlSinks | None:
    if not _bench_should_allocate_replay_meta(quiet=quiet, diagnostic_replay=diagnostic_replay):
        return None
    return ReplayXmlSinks(replay_meta_out={})


def _run_replay_with_bench_warning_capture(
    sid: str,
    *,
    mode: Literal["official_consolidation", "legal_pit"],
    diagnostic_replay: bool,
    replay_kwargs: Dict[str, Any],
) -> Tuple[Any, Counter[str]]:
    unexpected_keys = set(replay_kwargs) - {"quiet", "build_full_products", "oracle_selector"}
    if unexpected_keys:
        raise TypeError(f"unexpected bench replay kwargs: {sorted(unexpected_keys)}")
    quiet = bool(replay_kwargs.get("quiet", False))
    request = ReplayXmlRequest(
        parent_id=sid,
        mode=mode,
        quiet=quiet,
        build_full_products=bool(replay_kwargs.get("build_full_products", True)),
        oracle_selector=replay_kwargs.get("oracle_selector"),
    )
    timeline_env = "LAWVM_FI_ENABLE_TIMELINE_INVARIANTS"
    prev_timeline_env = os.environ.get(timeline_env)
    if fi_bench_timeline_invariants_enabled(diagnostic_replay=diagnostic_replay):
        os.environ[timeline_env] = "1"
    sinks = _bench_replay_sinks(quiet=quiet, diagnostic_replay=diagnostic_replay)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    try:
        with py_warnings.catch_warnings(record=True) as caught:
            py_warnings.simplefilter("always")
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                master = call_replay_xml(replay_xml, request=request, sinks=sinks)
    finally:
        if prev_timeline_env is None:
            os.environ.pop(timeline_env, None)
        else:
            os.environ[timeline_env] = prev_timeline_env
    warning_counts = _summarize_bench_warning_diagnostics(
        stdout_buf.getvalue(),
        stderr_buf.getvalue(),
        list(caught),
    )
    return master, _summarize_bench_replay_result_diagnostics(master, warning_counts)


def _filter_by_amend_count(
    corpus: List[Tuple[int, str]],
    min_amend: Optional[int] = None,
    max_amend: Optional[int] = None,
) -> List[Tuple[int, str]]:
    """Filter corpus by amendment count (first element of each tuple)."""
    result = [
        (c, sid) for c, sid in corpus if (min_amend is None or c >= min_amend) and (max_amend is None or c <= max_amend)
    ]
    skipped = len(corpus) - len(result)
    if max_amend == 0:
        label = "--filter-zero-amend"
    elif min_amend and min_amend > 0:
        label = "--filter-nonzero-amend"
    else:
        label = "--filter-amend-count"
    print(f"  {label}: {len(result)} kept, {skipped} excluded")
    return result


def _filter_by_decade(corpus: List[Tuple[int, str]], decade: str) -> List[Tuple[int, str]]:
    """Keep only statutes whose enactment year falls in the given decade string.

    decade: '1980s', '1990s', '2000s', etc.
    """
    result = [(c, sid) for c, sid in corpus if _decade(sid) == decade]
    skipped = len(corpus) - len(result)
    print(f"  --filter-decade {decade}: {len(result)} kept, {skipped} excluded")
    return result


def _repeal_fraction(data: bytes) -> float:
    """Fraction of <section> elements in oracle XML that contain kumottu attributions."""
    n_sections = data.count(b"<section")
    if n_sections == 0:
        return 0.0
    return min(1.0, count_kumottu_bytes(data) / n_sections)


def _is_empty_oracle(data: bytes) -> bool:
    """Return True if oracle looks silently-emptied (few sections, no kumottu, small body)."""
    import re as _re

    n_sections = data.count(b"<section")
    if n_sections > _EMPTY_MAX_SECTIONS:
        return False
    if count_kumottu_bytes(data) > 0:
        return False  # kumottu-annotated → use --filter-repealed for those
    body = _re.sub(rb"<[^>]+>", b" ", data).strip()
    return len(body) < _EMPTY_MAX_BYTES


def _apply_corpus_filters(
    corpus: List[Tuple[int, str]],
    filter_live: bool,
    filter_repealed: bool,
    filter_empty: bool = False,
    repealed_threshold: float = _REPEALED_THRESHOLD,
) -> List[Tuple[int, str]]:
    """Apply --filter-live, --filter-repealed, and/or --filter-empty in a single ZIP pass.

    filter_live:     skip statutes whose latest oracle is contentAbsent.
    filter_repealed: skip statutes where ≥threshold fraction of <section>
                     elements contain kumottu attributions (L:lla / A:lla) —
                     i.e. mostly-repealed statutes whose oracle is repeal annotations.
    filter_empty:    skip statutes where oracle has ≤3 sections, 0 kumottu, <2000
                     bytes of body text (silently-emptied — Finlex deleted sections
                     without kumottu annotations).
    """
    if not filter_live and not filter_repealed and not filter_empty:
        return corpus

    labels = []
    if filter_live:
        labels.append("--filter-live")
    if filter_repealed:
        labels.append(f"--filter-repealed (≥{repealed_threshold:.0%} kumottu)")
    if filter_empty:
        labels.append("--filter-empty (≤3sec/0kumottu/<2000b)")
    print(f"Filtering {len(corpus)} statutes ({', '.join(labels)})...")

    result: List[Tuple[int, str]] = []
    skipped_absent: List[str] = []
    skipped_repealed: List[str] = []
    skipped_empty: List[str] = []
    total = len(corpus)

    try:
        for i, (count, sid) in enumerate(corpus, 1):
            data = get_ground_truth_bytes(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
            if data is None:
                if filter_live:
                    skipped_absent.append(sid)
                else:
                    result.append((count, sid))
                continue

            if filter_live and _CONTENT_ABSENT_BYTES in data:
                skipped_absent.append(sid)
                continue

            if filter_repealed and _repeal_fraction(data) >= repealed_threshold:
                skipped_repealed.append(sid)
                continue

            if filter_empty and _is_empty_oracle(data):
                skipped_empty.append(sid)
                continue

            result.append((count, sid))
            if i % 500 == 0:
                print(
                    f"  {i}/{total}: kept={len(result)} "
                    f"absent={len(skipped_absent)} repealed={len(skipped_repealed)} "
                    f"empty={len(skipped_empty)}",
                    flush=True,
                )
    except Exception as e:
        print(f"ERROR in _apply_corpus_filters: {e}", file=sys.stderr)
        raise

    if skipped_absent:
        print(f"  Absent (contentAbsent): {len(skipped_absent)}  e.g. {skipped_absent[:3]}")
    if skipped_repealed:
        print(f"  Repealed (≥{repealed_threshold:.0%} kumottu): {len(skipped_repealed)}  e.g. {skipped_repealed[:3]}")
    if skipped_empty:
        print(f"  Empty (silently emptied): {len(skipped_empty)}  e.g. {skipped_empty[:3]}")
    print(f"  Kept: {len(result)}/{total}")
    return result


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    """LawVM/data/ — sibling of src/."""
    here = Path(__file__).resolve()
    # src/lawvm/tools/bench.py → src/lawvm/tools → src/lawvm → src → LawVM
    return here.parent.parent.parent.parent / "data"


def _history_path() -> Path:
    return _data_dir() / "benchmark_history.csv"


def _runs_dir() -> Path:
    d = _data_dir() / "bench_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Scoring (mirror of batch_test.py)
# ---------------------------------------------------------------------------


def _normalize(t: str) -> str:
    # Broader editorial cleanup: call the concrete FI normalizer directly, since
    # the generic registry signature is (str) -> str and does not expose the
    # strip_editorial flag.
    return normalize_finlex_oracle_comparison_text(t, strip_editorial=True)


_CLEAN_RE = re.compile(r"[^a-z0-9äöå]")


def _clean(t: str) -> str:
    return _CLEAN_RE.sub("", _normalize(t).lower())


def _clean_pre_normalized_oracle(t: str) -> str:
    """Clean oracle text returned by get_ground_truth without re-running FI projection rules."""
    return _CLEAN_RE.sub("", strip_editorial_annotations(t).lower())


def _clean_oracle_section_text(t: str) -> str:
    norm = get_oracle_text_normalizer("fi")
    return _CLEAN_RE.sub("", strip_editorial_annotations(norm(t)).lower())


def _comparison_ir(master: Any) -> Any:
    return comparison_ir_for_pit(master, query_type="in_force")


def _comparison_text(master: Any) -> str:
    return _serialize_ir_text(_comparison_ir(master))


def _levenshtein_comparison_text(master: Any) -> str:
    """Return the historical full replay text used by FI Levenshtein scoring.

    Structural scoring uses ``_comparison_ir`` so inactive PIT sections do not
    count as live legal structure.  The secondary full-text Levenshtein metric is
    a broad replay/oracle sanity check and historically compared the replay
    serializer against the consolidated oracle text.  Keep that surface stable:
    materialized/PIT projection can intentionally omit inactive context that the
    consolidated oracle text still carries, which makes the secondary metric
    measure projection policy rather than text replay agreement.
    """

    serialize_text = getattr(master, "serialize_text", None)
    if callable(serialize_text):
        return str(serialize_text())
    return _comparison_text(master)


def _levenshtein_ratio(left: str, right: str) -> float:
    """Return the same normalized indel similarity as ``python-Levenshtein.ratio``."""

    return float(_RapidFuzzIndel.normalized_similarity(left, right))


def _lev_sim_fast(sid: str, master: Any) -> float:
    """Full-text Levenshtein similarity — secondary metric alongside structural score.

    Reuses the already-replayed master to avoid a second replay call.
    Returns -1.0 on any error.
    """
    try:
        c_res = _clean(_levenshtein_comparison_text(master))
        c_truth = _clean_pre_normalized_oracle(
            get_ground_truth(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
        )
        if not c_truth:
            return -1.0
        return _levenshtein_ratio(c_res, c_truth)
    except Exception:
        return -1.0


def _is_oracle_crossheading_only(sd: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Return True when a section's only diffs are crossHeading heading facets.

    Replay correctly hoists ``crossHeading`` elements as section heading facets
    while the oracle (Finlex consolidated) drops them.  These are oracle
    deficiencies, not replay bugs — scoring them as errors misleads the metric.

    Note: ``sd["text"]`` may be 1 here because ``diff_semantic_trees`` counts
    a missing heading facet as both an event *and* a text diff.  That counts
    the same oracle deficiency twice, so we exclude ``text`` from the guard
    condition and rely on the events list alone.
    """
    if sd.get("structural", 0) or sd.get("label", 0):
        return False
    if not events:
        return False
    return all(
        e.get("kind") == "facet_removed" and e.get("facet_kind") == "heading"
        for e in events
    )


_DIGIT_PREFIX_RE = re.compile(r"^\d+[a-z]?\)\s+")


def _is_digit_renesting_mismatch(sd: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Return True when a section's diff is entirely from flat→merged digit renesting.

    The Finland parser normalises flat digit-item subsections (intro ending with
    ``:`` followed by ``N)``-prefixed siblings) into a single merged subsection
    with an intro facet and paragraph children.  Some oracle versions keep the
    original flat encoding (each numbered item in its own ``<subsection>``).
    When ALL text content is identical — just arranged differently — penalising
    the section is a false positive.

    Detection signature:
    - Has ``facet_removed(facet_kind='intro')`` — LawVM produced a johdanto
    - Has ``unit_missing_right(unit_kind='item')`` — LawVM's paragraph children
    - Has ``unit_missing_left(unit_kind='subsection')`` — oracle's flat siblings
    - No label changes
    - All event kinds are from the expected renesting set
    - After stripping ``N)`` prefixes from oracle texts, left and right text
      sets are identical (same content, different encoding)
    """
    if sd.get("label", 0):
        return False
    if not events:
        return False

    allowed_kinds = {
        "facet_removed",
        "unit_missing_right",
        "unit_missing_left",
        "wording_text_changed",
    }
    if not all(e.get("kind") in allowed_kinds for e in events):
        return False

    has_intro_removed = any(
        e.get("kind") == "facet_removed" and e.get("facet_kind") == "intro"
        for e in events
    )
    has_item_missing_right = any(
        e.get("kind") == "unit_missing_right" and e.get("unit_kind") == "item"
        for e in events
    )
    has_subsection_missing_left = any(
        e.get("kind") == "unit_missing_left" and e.get("unit_kind") == "subsection"
        for e in events
    )
    if not (has_intro_removed and has_item_missing_right and has_subsection_missing_left):
        return False

    # Verify text content is identical (modulo "N) " prefix on oracle flat items).
    # Collect all text fragments from each side, then check they form equal sets.
    left_texts: set[str] = set()
    right_texts: set[str] = set()

    def _strip_digit(t: str) -> str:
        return _DIGIT_PREFIX_RE.sub("", t, count=1)

    for e in events:
        kind = e.get("kind")
        lt = (e.get("left_text") or "").strip()
        rt = (e.get("right_text") or "").strip()
        if kind == "facet_removed" and e.get("facet_kind") == "intro":
            if lt:
                left_texts.add(lt)
        elif kind == "unit_missing_right":
            if lt:
                left_texts.add(lt)
        elif kind == "unit_missing_left":
            if rt:
                right_texts.add(_strip_digit(rt))
        elif kind == "wording_text_changed":
            if lt:
                left_texts.add(lt)
            if rt:
                right_texts.add(_strip_digit(rt))

    return bool(left_texts) and left_texts == right_texts


_TEXT_ONLY_EVENT_KINDS = {"wording_text_changed", "heading_text_changed", "intro_text_changed"}


def _is_wording_whitespace_only_diff(sd: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Return True when a section's diff consists entirely of whitespace-only text changes.

    Detects OCR-era source pathology: words fused together in old scanned source
    (e.g. 'kuolemansyynselvittämiseksi') that the oracle editorially corrected
    ('kuolemansyyn selvittämiseksi').  After removing all spaces the texts are
    identical, so the actual content is the same.

    Also handles ``heading_text_changed`` events where OCR run-together words appear
    in the heading facet (e.g. 'kustannustenkorvaaminen' → 'kustannusten korvaaminen').
    For headings, trailing periods and spaces are stripped before the space-removal
    comparison, matching ``_normalize_heading_for_diff`` semantics in semantic/diff.py.

    Safety invariants:
    - No structural or label differences are present.
    - Every event is ``wording_text_changed`` or ``heading_text_changed``.
    - For every event, both left and right texts are non-empty, and they are
      equal after normalisation (space removal; trailing period strip for headings).
    """
    if sd.get("label", 0) or sd.get("structural", 0):
        return False
    if not events:
        return False
    if not all(e.get("kind") in _TEXT_ONLY_EVENT_KINDS for e in events):
        return False
    for e in events:
        lt = e.get("left_text") or ""
        rt = e.get("right_text") or ""
        if e.get("kind") == "heading_text_changed":
            lt = lt.rstrip(". ")
            rt = rt.rstrip(". ")
        lt = lt.replace(" ", "")
        rt = rt.replace(" ", "")
        if not lt or not rt:
            return False
        if lt != rt:
            return False
    return True


# Use the jurisdiction-registered presentation detector (registered by
# the Finland module in its oracle_comparison.py). Bench itself does not
# import any jurisdiction package.
from lawvm.semantic.projection import is_presentation_structural_diff as _is_presentation_structural_diff


def _is_oracle_presentation_list_or_table(sd: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    # Delegation to the registered dispatcher. Default jurisdiction "fi"
    # for current FI-centric bench usage.
    return _is_presentation_structural_diff(sd, events, "fi")


def _section_diff_is_bench_neutralized(
    sd: dict[str, Any], events: list[dict[str, Any]]
) -> bool:
    """Return True when the bench does NOT penalise this section's diff.

    This is the single source of truth for "the bench neutralizes this
    divergence" — the same predicate ``_structural_sim`` uses to skip a
    diverging section.  A section is neutralized when its only diffs are:
    - oracle-side crossHeading deficiencies,
    - flat→merged digit renesting encoding differences,
    - whitespace-only (OCR word-fusion) wording changes, or
    - oracle presentation list/table layout differences.

    Editorial-only (kumottu tombstone) sections never reach this predicate —
    they are excluded upstream (``_sections_with_diffs`` drops ``editorial_only``
    and the ``non_editorial`` filter drops them again).  Callers that classify
    a raw section must therefore treat ``editorial_only`` as neutralized too.
    """
    return (
        _is_oracle_crossheading_only(sd, events)
        or _is_digit_renesting_mismatch(sd, events)
        or _is_wording_whitespace_only_diff(sd, events)
        or _is_oracle_presentation_list_or_table(sd, events)
    )


@dataclass(frozen=True, slots=True)
class _BenchSemanticScore:
    structural_similarity: float
    adjusted_levenshtein_similarity: float
    event_counts: Counter[str]
    # Events drawn only from PENALIZED sections (diverging and not neutralized).
    # ``event_counts`` counts every diverging section's events including
    # neutralized ones; the penalizing subset is what explains the structural
    # error and is the residue the unified-contract reconciliation checks
    # against (see lawvm.core.bench_contract.check_residue_reconciliation).
    penalized_event_counts: Counter[str] = field(default_factory=Counter)
    # amb (nondeterministic oracle-match) witnesses: one per section whose
    # penalty was forgiven because replay's text matched a genuine-but-not-chosen
    # oracle version (the oracle's version SELECTION is unreliable). Opaque
    # triage pointers, NOT reconciled against structural_err — they ride the
    # ``witnesses`` channel, never ``residue_buckets`` (an amb-neutralized
    # section has zero structural error, so a residue count would be phantom).
    amb_alternate_witnesses: tuple[str, ...] = ()


def _empty_bench_semantic_score(
    structural_similarity: float,
    adjusted_levenshtein_similarity: float,
) -> _BenchSemanticScore:
    return _BenchSemanticScore(
        structural_similarity=structural_similarity,
        adjusted_levenshtein_similarity=adjusted_levenshtein_similarity,
        event_counts=Counter(),
        penalized_event_counts=Counter(),
    )


def _semantic_section_score(sid: str, master: Any, *, text_scores: bool) -> _BenchSemanticScore:
    """Compute structural score and neutralized-section text score.

    The structural score already neutralizes known oracle/projection artifacts
    such as Finlex dropping a source ``crossHeading`` facet.  The ordinary full
    flattened Levenshtein path can count those same neutralized section diffs as
    text errors.  Preserve the raw full-text metric whenever structural has a
    real penalized diff; when structural is already perfect, allow the secondary
    text score to use the same neutralized section projection and never lower
    the raw full-text score.  Replay output is untouched; this is benchmark
    projection only.
    """
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.semantic.contracts import build_semantic_diff_support
    from lawvm.semantic.structure import (
        semantic_structure_from_ir,
        semantic_structure_from_oracle,
    )
    from lawvm.tools.section_keys import (
        extract_ir_sections,
        extract_oracle_section_alternates,
        extract_oracle_sections,
        oracle_amb_alternate_match,
        reconcile_unique_unscoped_aliases,
    )
    from lawvm.tools.structural_review import is_oracle_content_absent
    from lawvm.xml_ingest import xml_element_to_text

    oracle_root = get_ground_truth_tree(
        sid,
        selector=_BENCH_CONSOLIDATED_SELECTOR,
    )
    if is_oracle_content_absent(oracle_root):
        return _empty_bench_semantic_score(-1.0, -1.0)

    replay_ir = _comparison_ir(master)
    replay_sections = extract_ir_sections(replay_ir)
    oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}
    # amb candidate set: the oracle version renderings extract_oracle_sections
    # discarded, keyed by the SAME (pre-reconcile) section key (aliased keys get
    # no alternates -> fall through to status-quo penalize, safe).
    oracle_alternates = (
        extract_oracle_section_alternates(oracle_root) if oracle_root is not None else {}
    )
    replay_sections, oracle_sections = reconcile_unique_unscoped_aliases(
        replay_sections,
        oracle_sections,
    )

    if not oracle_sections and not replay_sections:
        return _empty_bench_semantic_score(1.0, 1.0 if text_scores else -1.0)

    raw_lev = -1.0
    if text_scores:
        raw_replay_text = _clean(_levenshtein_comparison_text(master))
        raw_oracle_text = _clean_pre_normalized_oracle(
            get_ground_truth(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
        )
        raw_lev = (
            -1.0
            if not raw_oracle_text
            else _levenshtein_ratio(raw_replay_text, raw_oracle_text)
        )
        if raw_lev < 0:
            text_scores = False
    section_keys = list(oracle_sections)
    section_keys.extend(key for key in replay_sections if key not in oracle_sections)

    non_editorial_count = 0
    penalized_count = 0
    event_counts: Counter[str] = Counter()
    penalized_event_counts: Counter[str] = Counter()
    amb_witnesses: list[str] = []
    replay_chunks: list[str] = []
    oracle_chunks: list[str] = []

    for key in section_keys:
        replay_node = replay_sections.get(key)
        oracle_node = oracle_sections.get(key)
        replay_sem = semantic_structure_from_ir(replay_node) if replay_node is not None else None
        oracle_sem = semantic_structure_from_oracle(oracle_node) if oracle_node is not None else None
        support = build_semantic_diff_support(replay_sem, oracle_sem)
        sd = support.get("semantic_diff")
        if not isinstance(sd, dict):
            continue
        if sd.get("kind") == "editorial_only":
            continue

        non_editorial_count += 1
        events = sd.get("events", [])
        if not isinstance(events, list):
            events = []
        has_diff = (
            bool(events)
            or bool(sd.get("structural", 0))
            or bool(sd.get("label", 0))
            or bool(sd.get("text", 0))
        )
        neutralized = has_diff and _section_diff_is_bench_neutralized(sd, events)
        if has_diff and not neutralized and replay_node is not None:
            # amb: replay matched a genuine-but-not-chosen oracle version of this
            # exact slot -> source-attested, forgive the penalty + warn (the
            # oracle's version selection, not replay, was the mismatch source).
            amb_witness = oracle_amb_alternate_match(
                key,
                _clean(irnode_to_text(replay_node)),
                oracle_alternates.get(key),
                _clean_oracle_section_text,
            )
            if amb_witness is not None:
                neutralized = True
                amb_witnesses.append(amb_witness)
                print(f"WARNING oracle suspect: version selection alternate match {amb_witness}")
        for event in events:
            if isinstance(event, dict):
                event_counts[event.get("kind", "unknown")] += 1
        if has_diff and not neutralized:
            penalized_count += 1
            penalized_events_here = 0
            for event in events:
                if isinstance(event, dict):
                    penalized_event_counts[event.get("kind", "unknown")] += 1
                    penalized_events_here += 1
            if penalized_events_here == 0:
                # Section penalized via structural/label/text diff flags but with
                # no typed event entries. Record a typed residue family so the
                # structural error is never silently unexplained (the
                # unified-contract reconciliation invariant).
                for flag in ("structural", "label", "text"):
                    if sd.get(flag, 0):
                        penalized_event_counts[f"section_diff_{flag}"] += 1

        if not text_scores:
            continue
        replay_text = irnode_to_text(replay_node) if replay_node is not None else ""
        oracle_text = xml_element_to_text(oracle_node) if oracle_node is not None else ""
        if neutralized:
            cleaned = _clean_oracle_section_text(oracle_text)
            replay_chunks.append(cleaned)
            oracle_chunks.append(cleaned)
        else:
            replay_chunks.append(_clean(replay_text))
            oracle_chunks.append(_clean_oracle_section_text(oracle_text))

    structural_similarity = (
        1.0
        if non_editorial_count == 0
        else 1.0 - penalized_count / non_editorial_count
    )
    if not text_scores:
        adjusted_lev = -1.0
    else:
        adjusted_lev = raw_lev
        if structural_similarity == 1.0:
            section_oracle_text = "".join(oracle_chunks)
            if section_oracle_text:
                section_lev = _levenshtein_ratio(
                    "".join(replay_chunks),
                    section_oracle_text,
                )
                adjusted_lev = max(adjusted_lev, section_lev)
    return _BenchSemanticScore(
        structural_similarity=structural_similarity,
        adjusted_levenshtein_similarity=adjusted_lev,
        event_counts=event_counts,
        penalized_event_counts=penalized_event_counts,
        amb_alternate_witnesses=tuple(amb_witnesses),
    )


def bench_penalized_section_keys(sid: str, master: Any) -> tuple[set[str], bool]:
    """Return ``(penalized_keys, oracle_absent)`` for a statute.

    ``penalized_keys`` is the exact set of section keys the bench's
    ``_structural_sim`` scores against (i.e. the sections that count against
    the similarity metric).  A key is penalized iff it has a non-editorial
    semantic diff with real events/structure AND none of the bench
    neutralization filters apply.  This is the view the bench actually
    penalizes — distinct from the consolidated-oracle text comparison
    ``oracle_check`` uses — and lets consumers (e.g. bench-triage) reconcile
    their classification with what the bench scores.

    The section keys are produced by ``compute_statute_section_diffs`` (via
    ``section_keys.extract_ir_sections`` / ``extract_oracle_sections``), the
    same key space ``oracle_check._classify_statute`` uses, so the two are
    directly comparable.
    """
    from lawvm.tools.structural_review import (
        compute_statute_section_diffs,
        _sections_with_diffs,
    )

    sections, oracle_absent = compute_statute_section_diffs(
        sid,
        oracle_selector_mode="bench_comparable",
        replay_master=master,
        support_mode="diff_only",
    )
    if oracle_absent:
        return set(), True
    non_editorial = {
        k: v
        for k, v in sections.items()
        if v.get("semantic_diff", {}).get("kind") != "editorial_only"
    }
    penalized: set[str] = set()
    for sec_key, sd, events in _sections_with_diffs({"sections": non_editorial}):
        if _section_diff_is_bench_neutralized(sd, events):
            continue
        if non_editorial.get(sec_key, {}).get("amb_alternate_match"):
            continue  # replay matched a non-chosen attested oracle version (amb)
        penalized.add(sec_key)
    return penalized, False


def _structural_sim(sid: str, master: Any) -> tuple[float, Counter[str]]:
    """Structural section score — consistent with ``structural-review --dump``.

    Returns ``(sim, event_counts)`` where:
    - ``sim`` is the fraction of non-editorial sections with no semantic diff
      events.  Sections only in oracle or only in replay each count as one
      divergence.  Editorial-only (kumottu tombstone) sections are excluded from
      both numerator and denominator.  Returns -1.0 if oracle is absent.
    - Sections whose only diffs are oracle-side crossHeading deficiencies
      (``facet_removed`` with ``facet_kind='heading'``) are not penalised.
    - Sections whose only diffs are flat→merged digit renesting encoding
      differences (same content, different structural layout) are not penalised.
    - Sections whose only diffs are wording changes where both sides are equal
      after removing all spaces (OCR word-fusion source pathology) are not penalised.
    - ``event_counts`` is a ``Counter`` of semantic diff event kinds across all
      diverging sections (e.g. ``unit_missing_left``, ``wording_text_changed``).
    """
    from lawvm.tools.structural_review import (
        compute_statute_section_diffs,
        _sections_with_diffs,
    )

    sections, oracle_absent = compute_statute_section_diffs(
        sid,
        oracle_selector_mode="bench_comparable",
        replay_master=master,
        support_mode="diff_only",
    )
    if oracle_absent:
        return -1.0, Counter()
    non_editorial = {
        k: v for k, v in sections.items()
        if v.get("semantic_diff", {}).get("kind") != "editorial_only"
    }
    if not non_editorial:
        return 1.0, Counter()
    diffs = _sections_with_diffs({"sections": non_editorial})
    event_counts: Counter[str] = Counter()
    penalised = 0
    for _sec_key, sd, events in diffs:
        for event in events:
            event_counts[event.get("kind", "unknown")] += 1
        if _section_diff_is_bench_neutralized(sd, events):
            continue
        if non_editorial.get(_sec_key, {}).get("amb_alternate_match"):
            continue  # replay matched a non-chosen attested oracle version (amb)
        penalised += 1
    return 1.0 - penalised / len(non_editorial), event_counts


def _score_one(
    sid: str,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    *,
    diagnostic_replay: bool = False,
) -> Tuple[str, float, str]:
    if is_known_missing_source(sid):
        return sid, -1.0, "SOURCE_UNAVAILABLE"
    try:
        master, _warning_counts = _run_replay_with_bench_warning_capture(
            sid,
            mode=mode,
            diagnostic_replay=diagnostic_replay,
            replay_kwargs={
                "quiet": not diagnostic_replay,
                "build_full_products": True,
                "oracle_selector": _BENCH_CONSOLIDATED_SELECTOR,
            },
        )
        sim, _events = _structural_sim(sid, master)
        if sim < 0:
            return sid, -1.0, "NO_TRUTH"
        return sid, sim, "OK"
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        return sid, -1.0, str(e)


def _score_one_with_warning_summary(
    sid: str,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    *,
    diagnostic_replay: bool = False,
    fast: bool = False,
    text_scores: bool = True,
) -> Tuple[str, float, str, float, Counter[str]]:
    """Return (sid, struct_sim, status, lev_sim, warning_counts).

    If *fast* is True, skip the expensive structural diff and use only
    the Levenshtein text similarity as both primary and secondary metric.
    This roughly halves per-statute bench time.
    """
    if is_known_missing_source(sid):
        return sid, -1.0, "SOURCE_UNAVAILABLE", -1.0, Counter()
    try:
        master, warning_counts = _run_replay_with_bench_warning_capture(
            sid,
            mode=mode,
            diagnostic_replay=diagnostic_replay,
            replay_kwargs={
                "quiet": not diagnostic_replay,
                "build_full_products": True,
                "oracle_selector": _BENCH_CONSOLIDATED_SELECTOR,
            },
        )
        if fast:
            lev_sim = _lev_sim_fast(sid, master) if text_scores else -1.0
            # Use lev as primary metric too; skip structural diff
            if lev_sim < 0:
                return sid, -1.0, "NO_TRUTH", lev_sim, warning_counts
            return sid, lev_sim, "OK", lev_sim, warning_counts
        semantic_score = _semantic_section_score(sid, master, text_scores=text_scores)
        sim = semantic_score.structural_similarity
        lev_sim = semantic_score.adjusted_levenshtein_similarity
        warning_counts = _merge_bench_structural_diagnostics(
            warning_counts,
            semantic_score.event_counts,
        )
        if sim < 0:
            return sid, -1.0, "NO_TRUTH", lev_sim, warning_counts
        return sid, sim, "OK", lev_sim, warning_counts
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        return sid, -1.0, str(e), -1.0, Counter()


# ---------------------------------------------------------------------------
# Unified cross-jurisdiction bench contract — Finland adapter
#
# The Finland comparator re-houses the existing structural/Levenshtein numbers
# into a contract BenchUnitResult without changing them. Registered at import
# time under the "fi" key; see lawvm.core.bench_comparator_registry and
# notes/UNIFIED_BENCH_CONTRACT.md.
# ---------------------------------------------------------------------------


def _fi_status_to_bench_status(fi_status: str) -> "BenchStatus":
    from lawvm.core.bench_contract import BenchStatus

    if fi_status == "OK":
        return BenchStatus.SCORED
    if fi_status == "SOURCE_UNAVAILABLE":
        return BenchStatus.SOURCE_UNAVAILABLE
    if fi_status == _ORACLE_STALE_DIAGNOSIS:
        return BenchStatus.ORACLE_STALE
    if fi_status in {"NO_TRUTH", "NO_ORACLE_TREE", "NO_ORACLE_SECS"}:
        return BenchStatus.NO_TRUTH
    # Any other string is an exception message — a genuine crash.
    return BenchStatus.CRASH


def _bench_oracle_provenance_witnesses(sid: str) -> tuple[str, ...]:
    """Surface the oracle-selection qualifier as opaque witness pointers.

    A score is valid even when the oracle it scored against was accepted only
    under the 180-day Finlex-ahead tolerance, or when other candidate artifacts
    were screened out as incomparable.  That qualifier must be *visible* — an
    ordinary OK score with no qualifier would silently hide that the oracle was
    tolerance-accepted.  This reads the same cached selection the score used
    (no re-selection, no score change) and returns witness strings only when
    there is something to surface.
    """
    from lawvm.finland.corpus import get_oracle_selection_provenance

    provenance = get_oracle_selection_provenance(
        sid, selector=_BENCH_CONSOLIDATED_SELECTOR
    )
    if provenance is None:
        return ()
    witnesses: list[str] = []
    if provenance.tolerance_applied:
        witnesses.append(
            "oracle_selected_under_180day_tolerance"
            f" version_tag={provenance.chosen_version_tag}"
        )
    if provenance.rejected_version_tags:
        witnesses.append(
            "oracle_candidates_rejected_incomparable="
            + ",".join(provenance.rejected_version_tags)
        )
    return tuple(witnesses)


def fi_bench_unit_result(
    sid: str,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    *,
    diagnostic_replay: bool = False,
    fast: bool = False,
    text_scores: bool = True,
) -> "BenchUnitResult":
    """Finland bench comparator — emit a contract ``BenchUnitResult`` for *sid*.

    Maps the existing Finland fidelity numbers onto the two contract error axes
    without changing them:

    - ``structural_err = 1 - structural section similarity`` (``None`` in the
      ``fast`` path, which has no structural axis).
    - ``text_err = 1 - adjusted Levenshtein similarity`` (``None`` when no text
      score was computed).
    - ``residue_buckets`` = the typed structural-diff event families drawn from
      the **penalized** sections, so the structural error reconciles with its
      residue (see ``check_residue_reconciliation``).
    """
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

    if is_known_missing_source(sid):
        return BenchUnitResult(unit_id=sid, status=BenchStatus.SOURCE_UNAVAILABLE)

    try:
        master, _warning_counts = _run_replay_with_bench_warning_capture(
            sid,
            mode=mode,
            diagnostic_replay=diagnostic_replay,
            replay_kwargs={
                "quiet": not diagnostic_replay,
                "build_full_products": True,
                "oracle_selector": _BENCH_CONSOLIDATED_SELECTOR,
            },
        )
        if fast:
            lev_sim = _lev_sim_fast(sid, master) if text_scores else -1.0
            if lev_sim < 0:
                return BenchUnitResult(unit_id=sid, status=BenchStatus.NO_TRUTH)
            # Fast path has no structural axis — only the text axis is attempted.
            return BenchUnitResult(
                unit_id=sid,
                status=BenchStatus.SCORED,
                structural_err=None,
                text_err=1.0 - lev_sim,
                witnesses=_bench_oracle_provenance_witnesses(sid),
            )

        score = _semantic_section_score(sid, master, text_scores=text_scores)
        sim = score.structural_similarity
        if sim < 0:
            return BenchUnitResult(unit_id=sid, status=BenchStatus.NO_TRUTH)
        lev_sim = score.adjusted_levenshtein_similarity
        text_err = None if lev_sim < 0 else 1.0 - lev_sim
        residue = {k: int(v) for k, v in score.penalized_event_counts.items() if v}
        return BenchUnitResult(
            unit_id=sid,
            status=BenchStatus.SCORED,
            structural_err=1.0 - sim,
            text_err=text_err,
            residue_buckets=residue,
            witnesses=(
                *_bench_oracle_provenance_witnesses(sid),
                *score.amb_alternate_witnesses,
            ),
        )
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        return BenchUnitResult(
            unit_id=sid,
            status=BenchStatus.CRASH,
            witnesses=(str(e),),
        )


def _register_fi_bench_comparator() -> None:
    from lawvm.core.bench_comparator_registry import register_bench_comparator

    register_bench_comparator("fi", fi_bench_unit_result)


_register_fi_bench_comparator()


def _section_score(
    sid: str,
    *,
    diagnostic_replay: bool = False,
) -> Tuple[str, float, float, str]:
    """Return (sid, text_sim, section_sim, status).

    text_sim:    standard Levenshtein on full text (existing metric).
    section_sim: mean per-section Levenshtein similarity.

    Sections matched by normalized label (same normalization as diff.py).
    Oracle sections with no matching replay section score 0.
    Extra replay sections not in oracle are ignored (they don't inflate the mean).
    """
    if is_known_missing_source(sid):
        return sid, -1.0, -1.0, "SOURCE_UNAVAILABLE"
    try:
        from lawvm.tools.section_keys import (
            extract_ir_sections,
            extract_oracle_sections,
            reconcile_unique_unscoped_aliases,
        )

        master, _warning_counts = _run_replay_with_bench_warning_capture(
            sid,
            mode="official_consolidation",
            diagnostic_replay=diagnostic_replay,
            replay_kwargs={
                "quiet": not diagnostic_replay,
                "build_full_products": True,
                "oracle_selector": _BENCH_CONSOLIDATED_SELECTOR,
            },
        )

        # Full-text similarity (existing metric)
        c_res = _clean(_levenshtein_comparison_text(master))
        c_truth = _clean_pre_normalized_oracle(
            get_ground_truth(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
        )
        if not c_truth:
            return sid, -1.0, -1.0, "NO_TRUTH"
        text_sim = _levenshtein_ratio(c_res, c_truth)

        # Section-level similarity. Keys are full provision paths where needed,
        # so duplicate section numbers across chapters compare correctly.
        oracle_root = get_ground_truth_tree(
            sid,
            selector=_BENCH_CONSOLIDATED_SELECTOR,
        )
        if oracle_root is None:
            return sid, text_sim, -1.0, "NO_ORACLE_TREE"

        replay_secs = extract_ir_sections(_comparison_ir(master))
        oracle_secs = extract_oracle_sections(oracle_root)
        replay_secs, oracle_secs = reconcile_unique_unscoped_aliases(replay_secs, oracle_secs)

        if not oracle_secs:
            return sid, text_sim, -1.0, "NO_ORACLE_SECS"

        scores: List[float] = []
        for key, o_el in oracle_secs.items():
            r_node = replay_secs.get(key)
            if r_node is None:
                scores.append(0.0)
                continue
            from lawvm.core.ir_helpers import irnode_to_text
            from lawvm.xml_ingest import xml_element_to_text
            norm = get_oracle_text_normalizer("fi")

            r_text = re.sub(r"[^a-z0-9äöå]", "", irnode_to_text(r_node).lower())
            o_raw = xml_element_to_text(o_el)
            o_norm = norm(o_raw)
            o_text = re.sub(r"[^a-z0-9äöå]", "", o_norm.lower())
            if not r_text and not o_text:
                scores.append(1.0)
            elif not r_text or not o_text:
                scores.append(0.0)
            else:
                scores.append(_levenshtein_ratio(r_text, o_text))

        section_sim = sum(scores) / len(scores)
        return sid, text_sim, section_sim, "OK"

    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        return sid, -1.0, -1.0, str(e)


def _section_score_with_warning_summary(
    sid: str,
    *,
    diagnostic_replay: bool = False,
) -> Tuple[str, float, float, str, Counter[str]]:
    if is_known_missing_source(sid):
        return sid, -1.0, -1.0, "SOURCE_UNAVAILABLE", Counter()
    try:
        from lawvm.tools.section_keys import (
            extract_ir_sections,
            extract_oracle_sections,
            reconcile_unique_unscoped_aliases,
        )

        master, warning_counts = _run_replay_with_bench_warning_capture(
            sid,
            mode="official_consolidation",
            diagnostic_replay=diagnostic_replay,
            replay_kwargs={
                "quiet": not diagnostic_replay,
                "build_full_products": True,
                "oracle_selector": _BENCH_CONSOLIDATED_SELECTOR,
            },
        )

        c_res = _clean(_levenshtein_comparison_text(master))
        c_truth = _clean_pre_normalized_oracle(
            get_ground_truth(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
        )
        if not c_truth:
            return sid, -1.0, -1.0, "NO_TRUTH", warning_counts
        text_sim = _levenshtein_ratio(c_res, c_truth)

        oracle_root = get_ground_truth_tree(
            sid,
            selector=_BENCH_CONSOLIDATED_SELECTOR,
        )
        if oracle_root is None:
            return sid, text_sim, -1.0, "NO_ORACLE_TREE", warning_counts

        replay_secs = extract_ir_sections(_comparison_ir(master))
        oracle_secs = extract_oracle_sections(oracle_root)
        replay_secs, oracle_secs = reconcile_unique_unscoped_aliases(replay_secs, oracle_secs)

        if not oracle_secs:
            return sid, text_sim, -1.0, "NO_ORACLE_SECS", warning_counts

        scores: List[float] = []
        for key, o_el in oracle_secs.items():
            r_node = replay_secs.get(key)
            if r_node is None:
                scores.append(0.0)
                continue
            from lawvm.core.ir_helpers import irnode_to_text
            from lawvm.xml_ingest import xml_element_to_text
            norm = get_oracle_text_normalizer("fi")

            r_text = re.sub(r"[^a-z0-9äöå]", "", irnode_to_text(r_node).lower())
            o_raw = xml_element_to_text(o_el)
            o_norm = norm(o_raw)
            o_text = re.sub(r"[^a-z0-9äöå]", "", o_norm.lower())
            if not r_text and not o_text:
                scores.append(1.0)
            elif not r_text or not o_text:
                scores.append(0.0)
            else:
                scores.append(_levenshtein_ratio(r_text, o_text))

        section_sim = sum(scores) / len(scores)
        return sid, text_sim, section_sim, "OK", warning_counts

    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        return sid, -1.0, -1.0, str(e), Counter()


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def _load_corpus(corpus_path: str) -> List[Tuple[int, str]]:
    """Load corpus CSV. Format: N,YEAR/NUM (N = amendment count)."""
    with open(corpus_path, newline="") as f:
        rows = list(csv.reader(f))
    result = []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            count = int(row[0])
            sid = row[1].strip()
        except (ValueError, IndexError):
            continue
        result.append((count, sid))
    return result


def _default_corpus_path() -> str:
    here = Path(__file__).resolve()
    lawvm_dir = here.parent.parent.parent.parent
    # Primary: data/finland/bench_corpus.csv (3591 curated statutes)
    primary = lawvm_dir / "data" / "finland" / "bench_corpus.csv"
    if primary.exists():
        return str(primary)
    # Fallback: .tmp/batch_test_list.csv (legacy)
    return str(lawvm_dir / ".tmp" / "batch_test_list.csv")


# ---------------------------------------------------------------------------
# Verified-statutes ledger
# ---------------------------------------------------------------------------


def _verified_statutes_path() -> Path:
    here = Path(__file__).resolve()
    lawvm_dir = here.parent.parent.parent.parent
    return lawvm_dir / "notes" / "VERIFIED_STATUTES.csv"


def _load_verified_statutes() -> Dict[str, tuple[str, int]]:
    """Load notes/VERIFIED_STATUTES.csv and return {statute_id: (status, min_correct_pct)}.

    For statutes with multiple rows, the most recent row by verified_date wins.
    Comment lines (starting with #) and the header row are skipped.
    Returns an empty dict if the file does not exist.
    min_correct_pct=0 means "no regression guard" (oracle was already wrong/unknown at classification).
    """
    path = _verified_statutes_path()
    if not path.exists():
        return {}

    latest: Dict[str, tuple[str, str, int]] = {}  # sid -> (date, status, min_pct)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(
            (line for line in f if not line.lstrip().startswith("#")),
        )
        for row in reader:
            sid = (row.get("statute_id") or "").strip()
            status = (row.get("status") or "").strip()
            date = (row.get("verified_date") or "").strip()
            try:
                min_pct = int(row.get("min_correct_pct") or 0)
            except (ValueError, TypeError):
                min_pct = 0
            if not sid or not status:
                continue
            prev = latest.get(sid)
            if prev is None or date > prev[0]:
                latest[sid] = (date, status, min_pct)

    return {sid: (val[1], val[2]) for sid, val in latest.items()}


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------


def _compute_stats(results: List[Tuple[str, float, str]]) -> Dict:
    ok = [(sid, sim) for sid, sim, st in results if sim >= 0]
    n = len(ok)
    if n == 0:
        return dict(mean=0.0, n=0, perfect=0, above_99=0, above_95=0, below_90=0, errors=len(results) - n)
    sims = [sim for _, sim in ok]
    return dict(
        mean=sum(sims) / n,
        n=n,
        perfect=sum(1 for s in sims if s >= 0.9999),
        above_99=sum(1 for s in sims if s >= 0.99),
        above_95=sum(1 for s in sims if s >= 0.95),
        below_90=sum(1 for s in sims if s < 0.90),
        errors=len(results) - n,
    )


def _oracle_stale_adjusted_stats(
    results: List[Tuple[int, str, float, str, float]],
    *,
    workers: int,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    diagnostic_counts: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[Dict[str, Any]]:
    """Compute an oracle-stale-aware headline mean over the current bench rows.

    This is a reporting policy only: raw bench scores remain unchanged.  The
    adjusted headline excludes rows classified as ORACLE_STALE by oracle-check,
    which is where future-dated oracle-cutoff statutes land.
    """

    valid_rows = [(sid, sim) for _, sid, sim, _status, _elapsed in results if sim >= 0]
    if not valid_rows:
        return None

    oracle_checks = _run_oracle_checks_parallel(
        [sid for sid, _sim in valid_rows],
        workers=max(1, min(workers, len(valid_rows))),
        mode=mode,
        progress=False,
    )
    if diagnostic_counts is not None:
        for sid, oracle_info in oracle_checks.items():
            top_diagnosis = str(oracle_info.get("top_diagnosis") or "").strip()
            if top_diagnosis and top_diagnosis != "UNKNOWN":
                counts = diagnostic_counts.setdefault(sid, {})
                key = f"oracle_check:top_diagnosis:{top_diagnosis}"
                counts[key] = counts.get(key, 0) + 1

    kept: List[float] = []
    excluded: List[str] = []
    for sid, sim in valid_rows:
        oracle_info = oracle_checks.get(sid)
        if oracle_info and str(oracle_info.get("top_diagnosis") or "") == _ORACLE_STALE_DIAGNOSIS:
            excluded.append(sid)
            continue
        kept.append(sim)

    if not kept:
        return {
            "mean": None,
            "n": 0,
            "excluded": excluded,
            "oracle_checked": len(valid_rows),
        }

    return {
        "mean": sum(kept) / len(kept),
        "n": len(kept),
        "excluded": excluded,
        "oracle_checked": len(valid_rows),
    }


# ---------------------------------------------------------------------------
# Run benchmark
# ---------------------------------------------------------------------------


def _score_one_with_meta(
    args: Tuple[int, str, Any, Literal["official_consolidation", "legal_pit"], bool, bool],
) -> Tuple[int, str, float, str, float, str, float, Dict[str, int]]:
    """Wrapper for parallel execution — takes (count, sid, diagnostic_replay, mode, fast).

    Returns (count, sid, struct_sim, status, elapsed, warning_summary, lev_sim, warning_counts).
    """
    count, sid, diagnostic_replay, mode, fast, text_scores = args
    t0 = time.time()
    sid, sim, status, lev_sim, warning_counts = _score_one_with_warning_summary(
        sid,
        mode=mode,
        diagnostic_replay=diagnostic_replay,
        fast=fast,
        text_scores=text_scores,
    )
    elapsed = time.time() - t0
    return (
        count,
        sid,
        sim,
        status,
        elapsed,
        _format_bench_warning_summary(warning_counts),
        lev_sim,
        dict(warning_counts),
    )


def _score_one_with_meta_section(
    args: Tuple[int, str, bool, Literal["official_consolidation", "legal_pit"]],
) -> Tuple[int, str, float, float, str, float, str, Dict[str, int]]:
    """Wrapper for parallel execution with --section-score.

    Takes (count, sid, diagnostic_replay, mode); returns
    (count, sid, text_sim, section_sim, status, elapsed, warning_summary, warning_counts).
    """
    count, sid, diagnostic_replay, _mode = args
    t0 = time.time()
    sid, text_sim, section_sim, status, warning_counts = _section_score_with_warning_summary(
        sid,
        diagnostic_replay=diagnostic_replay,
    )
    elapsed = time.time() - t0
    return (
        count,
        sid,
        text_sim,
        section_sim,
        status,
        elapsed,
        _format_bench_warning_summary(warning_counts),
        dict(warning_counts),
    )


def _run_benchmark_section(
    corpus: List[Tuple[int, str]],
    verbose: bool = True,
    workers: int = 1,
    diagnostic_replay: bool = False,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    diagnostic_summaries_out: Optional[Dict[str, str]] = None,
    diagnostic_counts_out: Optional[Dict[str, Dict[str, int]]] = None,
) -> List[Tuple[int, str, float, float, str, float]]:
    """Run benchmark with section-level scoring.

    Returns list of (count, sid, text_sim, section_sim, status, elapsed).
    """
    corpus_indexed = sorted(enumerate(corpus), key=lambda x: -x[1][0])
    total = len(corpus)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        results: List[Tuple[int, str, float, float, str, float]] = cast(List[Tuple[int, str, float, float, str, float]], [None] * total)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_score_one_with_meta_section, (count, sid, diagnostic_replay, mode)): orig_idx
                for orig_idx, (count, sid) in corpus_indexed
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                result = future.result()
                results[idx] = result[:6]
                done += 1
                if diagnostic_summaries_out is not None:
                    diagnostic_summaries_out[result[1]] = result[6]
                if diagnostic_counts_out is not None:
                    diagnostic_counts_out[result[1]] = result[7]
                if verbose:
                    count, sid, text_sim, section_sim, status, elapsed, warning_summary, _counts = result
                    t_err = _format_error_for_display(text_sim, status)
                    s_err = f"{(1 - section_sim) * 100:.2f}%" if section_sim >= 0 else "---"
                    extra = "" if status == "OK" else f"  {status}"
                    print(
                        f"[{done}/{total}] {count:2d}amend {sid:12s}"
                        f" → txt {t_err:>7s}  sec {s_err:>7s} ({elapsed:.1f}s){extra}"
                        f"{warning_summary}"
                    )
        return results

    results = []
    for i, (count, sid) in enumerate((c for _, c in corpus_indexed), start=1):
        t0 = time.time()
        sid, text_sim, section_sim, status, warning_counts = _section_score_with_warning_summary(
            sid,
            diagnostic_replay=diagnostic_replay,
        )
        elapsed = time.time() - t0
        results.append((count, sid, text_sim, section_sim, status, elapsed))
        warning_summary = _format_bench_warning_summary(warning_counts)
        if diagnostic_summaries_out is not None:
            diagnostic_summaries_out[sid] = warning_summary
        if diagnostic_counts_out is not None:
            diagnostic_counts_out[sid] = dict(warning_counts)
        if verbose:
            t_err = _format_error_for_display(text_sim, status)
            s_err = f"{(1 - section_sim) * 100:.2f}%" if section_sim >= 0 else "---"
            extra = "" if status == "OK" else f"  {status}"
            print(
                f"[{i}/{total}] {count:2d}amend {sid:12s} → txt {t_err:>7s}  sec {s_err:>7s}"
                f" ({elapsed:.1f}s){extra}{warning_summary}"
            )
    return results


def _run_benchmark(
    corpus: List[Tuple[int, str]],
    verbose: bool = True,
    workers: int = 1,
    diagnostic_replay: bool = False,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    fast: bool = False,
    text_scores: bool = True,
    diagnostic_summaries_out: Optional[Dict[str, str]] = None,
    diagnostic_counts_out: Optional[Dict[str, Dict[str, int]]] = None,
) -> Tuple[List[Tuple[int, str, float, str, float]], Optional[Dict[str, float]]]:
    """Run benchmark, optionally parallel with ProcessPoolExecutor.

    Returns (results, lev_sims) where:
    - results is a list of (count, sid, struct_sim, status, elapsed)
    - lev_sims is a dict of {sid: lev_sim} for secondary display
    """
    # Sort by amendment count descending so longest chains start first,
    # minimizing tail-straggler wait time with parallel workers.
    corpus_indexed = sorted(enumerate(corpus), key=lambda x: -x[1][0])
    total = len(corpus)

    collect_lev_sims = text_scores or fast
    lev_sims: Optional[Dict[str, float]] = {} if collect_lev_sims else None

    if workers > 1:
        # Pre-warm: populate caches in the main process so forked workers
        # inherit them via copy-on-write.  This eliminates per-worker
        # DB/ZIP setup overhead and SQLite contention.
        # _latest_consolidated_path_by_statute() no longer has its own
        # @lru_cache; it delegates to corpus_store.oracle_path_index() which
        # caches on the CorpusStore instance.  Calling it here pre-warms that
        # instance-level cache so workers inherit a ready index.
        from lawvm.finland.amendment_selection import amendment_children_by_parent as _amendment_children_by_parent
        from lawvm.finland.corpus import (
            _get_corpus_store,
            _latest_consolidated_path_by_statute,
        )

        _get_corpus_store()  # singleton store
        _amendment_children_by_parent()  # amendment index
        _latest_consolidated_path_by_statute()  # oracle path index (instance-cached)

        from concurrent.futures import ProcessPoolExecutor, as_completed

        results: List[Tuple[int, str, float, str, float]] = cast(List[Tuple[int, str, float, str, float]], [None] * total)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_score_one_with_meta, (count, sid, diagnostic_replay, mode, fast, text_scores)): orig_idx
                for orig_idx, (count, sid) in corpus_indexed
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                result = future.result()
                count, sid, sim, status, elapsed, warning_summary, lev_sim, warning_counts = result
                results[idx] = (count, sid, sim, status, elapsed)
                if lev_sims is not None:
                    lev_sims[sid] = lev_sim
                if diagnostic_summaries_out is not None:
                    diagnostic_summaries_out[sid] = warning_summary
                if diagnostic_counts_out is not None:
                    diagnostic_counts_out[sid] = warning_counts
                done += 1
                if verbose:
                    err = _format_error_for_display(sim, status)
                    lev_str = f" lev {(1 - lev_sim) * 100:.2f}%" if lev_sim >= 0 else ""
                    extra = "" if status == "OK" else f"  {status}"
                    print(f"[{done}/{total}] {count:2d}amend {sid:12s} → err {err:>7s}{lev_str} ({elapsed:.1f}s){extra}{warning_summary}")
        return results, lev_sims

    # Sequential fallback
    results = []
    for i, (count, sid) in enumerate((c for _, c in corpus_indexed), start=1):
        t0 = time.time()
        sid, sim, status, lev_sim, warning_counts = _score_one_with_warning_summary(
            sid,
            mode=mode,
            diagnostic_replay=diagnostic_replay,
            fast=fast,
            text_scores=text_scores,
        )
        elapsed = time.time() - t0
        results.append((count, sid, sim, status, elapsed))
        if lev_sims is not None:
            lev_sims[sid] = lev_sim
        warning_summary = _format_bench_warning_summary(warning_counts)
        if diagnostic_summaries_out is not None:
            diagnostic_summaries_out[sid] = warning_summary
        if diagnostic_counts_out is not None:
            diagnostic_counts_out[sid] = dict(warning_counts)
        if verbose:
            err = _format_error_for_display(sim, status)
            lev_str = f" lev {(1 - lev_sim) * 100:.2f}%" if lev_sim >= 0 else ""
            extra = "" if status == "OK" else f"  {status}"
            print(f"[{i}/{total}] {count:2d}amend {sid:12s} → err {err:>7s}{lev_str} ({elapsed:.1f}s){extra}{warning_summary}")
    return results, lev_sims


def _bench_diagnostics_sidecar_path(run_path: Path) -> Path:
    return run_path.with_suffix(".diagnostics.jsonl")


def _save_bench_diagnostic_sidecar(
    *,
    run_path: Path,
    label: str,
    results: List[Tuple[int, str, float, str, float]],
    diagnostic_counts: Dict[str, Dict[str, int]],
) -> Path | None:
    """Persist structured per-statute diagnostic rows beside the bench CSV."""
    if not diagnostic_counts:
        return None
    out_path = _bench_diagnostics_sidecar_path(run_path)
    row_count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for amend_count, sid, _sim, _status, _elapsed in results:
            counts = diagnostic_counts.get(sid) or {}
            for row in bench_diagnostic_sidecar_rows(
                label=label,
                statute_id=sid,
                amendment_count=amend_count,
                counts=counts,
            ):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                row_count += 1
    if row_count == 0:
        if out_path.exists():
            out_path.unlink()
        return None
    return out_path


# ---------------------------------------------------------------------------
# Save / load run
# ---------------------------------------------------------------------------


def _save_run(
    results: List[Tuple[int, str, float, str, float]],
    label: str,
    timestamp: str,
    section_results: Optional[List[Tuple[int, str, float, float, str, float]]] = None,
    lev_sims: Optional[Dict[str, float]] = None,
    diagnostic_summaries: Optional[Dict[str, str]] = None,
) -> Path:
    """Save per-statute results to bench_runs/.

    If section_results is provided (from --section-score), a section_similarity
    column is merged into the output CSV.
    If lev_sims is provided, a lev_similarity column is added.  If
    diagnostic_summaries is provided, a diagnostics_summary column preserves replay
    diagnostics that were already printed live during the run.
    """
    fname = f"{timestamp.replace(':', '').replace('-', '')[:15]}_{label}.csv"
    path = _runs_dir() / fname

    # Build section_sim lookup keyed by sid
    sec_sim_map: Dict[str, float] = {}
    if section_results is not None:
        for row in section_results:
            _count, _sid, _text_sim, _section_sim, _status, _elapsed = row
            sec_sim_map[_sid] = _section_sim

    has_lev = lev_sims is not None
    has_diagnostics = diagnostic_summaries is not None
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        header = ["amendments", "statute_id", "similarity"]
        if sec_sim_map and has_lev:
            header += ["section_similarity", "lev_similarity"]
        elif sec_sim_map:
            header.append("section_similarity")
        elif has_lev:
            header.append("lev_similarity")
        header += ["status", "elapsed_s"]
        if has_diagnostics:
            header.append("diagnostics_summary")
        w.writerow(header)
        for count, sid, sim, status, elapsed in results:
            sim_str = _format_similarity_for_csv(sim, status)
            row_vals: List[Any] = [count, sid, sim_str]
            if sec_sim_map:
                ssim = sec_sim_map.get(sid, -1.0)
                row_vals.append(_format_similarity_for_csv(ssim, status))
            if has_lev:
                assert lev_sims is not None
                lsim = lev_sims.get(sid, -1.0)
                row_vals.append(_format_similarity_for_csv(lsim, status))
            row_vals += [status, f"{elapsed:.1f}"]
            if has_diagnostics:
                row_vals.append((diagnostic_summaries or {}).get(sid, ""))
            w.writerow(row_vals)
    return path


def _bench_evidence_report_path(run_path: Path) -> Path:
    return run_path.with_suffix(".evidence.json")


def _write_bench_evidence_surface(
    *,
    run_path: Path,
    label: str,
    timestamp: str,
    mode: str,
    corpus_path: str,
    stats: Dict[str, Any],
    results: List[Tuple[int, str, float, str, float]],
    workers: int,
    section_score: bool,
    fast_mode: bool,
    diagnostic_replay: bool,
    lev_sims: Optional[Dict[str, float]],
    diagnostic_summaries: Dict[str, str],
    oracle_stale_adjusted: Optional[Dict[str, Any]],
    selection_as_of: Optional[str] = None,
) -> Path:
    diagnostic_summary_counts: Counter[str] = Counter(
        summary for summary in diagnostic_summaries.values() if summary
    )
    status_counts: Counter[str] = Counter(status for _count, _sid, _sim, status, _elapsed in results)
    report = finland_bench_run_evidence_surface(
        {
            "label": label,
            "timestamp": timestamp,
            "mode": mode,
            "corpus_path": corpus_path,
            "run_path": str(run_path),
            "history_path": str(_history_path()),
            "stats": stats,
            "status_counts": dict(sorted(status_counts.items())),
            "diagnostic_summary_counts": dict(sorted(diagnostic_summary_counts.items())),
            "diagnostic_summary_row_count": len(diagnostic_summaries),
            "section_score": section_score,
            "levenshtein_score": bool(lev_sims),
            "worker_count": workers,
            "fast_mode": fast_mode,
            "diagnostic_replay": diagnostic_replay,
            "oracle_stale_adjusted": oracle_stale_adjusted or {},
            "selection_as_of": selection_as_of or "",
        }
    )
    path = _bench_evidence_report_path(run_path)
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_run_by_label(label: str) -> Optional[List[Tuple[str, float]]]:
    """Load per-statute results for a labeled run. Returns [(sid, sim)]."""
    runs_dir = _runs_dir()
    # Find file matching label
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]  # latest if multiple
    results = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["statute_id"]
            sim_str = row["similarity"]
            try:
                sim = float(sim_str)
            except ValueError:
                sim = -1.0
            results.append((sid, sim))
    return results


def _load_run_lev_sims(label: str) -> Optional[Dict[str, float]]:
    """Load lev_similarity column from a past run, if present."""
    runs_dir = _runs_dir()
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]
    result: Dict[str, float] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if "lev_similarity" not in (reader.fieldnames or []):
            return None
        for row in reader:
            sid = row["statute_id"]
            try:
                result[sid] = float(row["lev_similarity"])
            except (ValueError, KeyError):
                result[sid] = -1.0
    return result


# ---------------------------------------------------------------------------
# History CSV
# ---------------------------------------------------------------------------

HISTORY_HEADER = [
    "timestamp",
    "label",
    "mean_score",
    "n_statutes",
    "n_perfect",
    "n_above_99",
    "n_above_95",
    "n_below_90",
]


def _append_history(timestamp: str, label: str, stats: Dict) -> None:
    path = _history_path()
    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(HISTORY_HEADER)
        w.writerow(
            [
                timestamp,
                label,
                f"{stats['mean']:.4f}",
                stats["n"],
                stats["perfect"],
                stats["above_99"],
                stats["above_95"],
                stats["below_90"],
            ]
        )


def _load_history() -> List[Dict]:
    path = _history_path()
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Display functions
# ---------------------------------------------------------------------------


# Opt-in environment flag: when set to a truthy value, the Finland bench ALSO
# renders the shared cross-jurisdiction unified summary (via the bench contract)
# in addition to its legacy summary. Default (unset) leaves FI output exactly as
# it has always been, so the many FI bench tests stay green untouched.
_FI_BENCH_UNIFIED_ENV = "LAWVM_BENCH_UNIFIED"


def _fi_bench_unified_enabled() -> bool:
    return os.environ.get(_FI_BENCH_UNIFIED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _fi_unit_results_from_rows(
    flat: List[Tuple[str, float, str]],
    *,
    section_sims: Optional[Dict[str, float]] = None,
) -> list["BenchUnitResult"]:
    """Build contract ``BenchUnitResult``s from already-computed FI bench rows.

    Maps the existing per-statute numbers onto the two contract axes WITHOUT
    re-running replay (the expensive work is already done):

    - ``text_err = 1 - text_sim`` when a text score was computed (``sim >= 0``).
    - ``structural_err = 1 - section_sim`` when section scoring ran for the row
      (``section_sims`` provides it), else ``None`` (axis not attempted on the
      text-only run). When a structural axis is present the typed residue is not
      available from the flat rows, so reconciliation is skipped for those rows
      by leaving ``structural_err`` unset whenever no section score exists.
    - non-scored / crashed rows map onto the matching ``BenchStatus`` so the
      scored/non-scored/crashed partition matches the legacy summary exactly.
    """
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

    _status_map = {
        "NO_TRUTH": BenchStatus.NO_TRUTH,
        "SOURCE_UNAVAILABLE": BenchStatus.SOURCE_UNAVAILABLE,
        _ORACLE_STALE_DIAGNOSIS: BenchStatus.ORACLE_STALE,
    }
    out: list[BenchUnitResult] = []
    for sid, sim, status in flat:
        if sim < 0:
            mapped = _status_map.get(status)
            if mapped is not None:
                out.append(BenchUnitResult(unit_id=sid, status=mapped))
            else:
                # Genuine exception (status == str(exception)) — a crash.
                out.append(
                    BenchUnitResult(
                        unit_id=sid, status=BenchStatus.CRASH, witnesses=(status,)
                    )
                )
            continue
        structural_err: Optional[float] = None
        if section_sims is not None:
            sec = section_sims.get(sid)
            if sec is not None and sec >= 0:
                structural_err = 1.0 - sec
        out.append(
            BenchUnitResult(
                unit_id=sid,
                status=BenchStatus.SCORED,
                structural_err=structural_err,
                text_err=1.0 - sim,
            )
        )
    return out


def _show_unified_summary_fi(
    flat: List[Tuple[str, float, str]],
    label: str,
    *,
    section_sims: Optional[Dict[str, float]] = None,
) -> None:
    """Render the shared cross-jurisdiction summary for FI (env-var gated)."""
    from lawvm.core.bench_aggregate import render_summary

    unit_results = _fi_unit_results_from_rows(flat, section_sims=section_sims)
    for line in render_summary(unit_results, label, jurisdiction="fi"):
        print(line)


def _show_summary(
    results: List[Tuple[int, str, float, str, float]],
    label: str,
    *,
    oracle_stale_adjusted: Optional[Dict[str, Any]] = None,
    verified: Optional[Dict[str, tuple[str, int]]] = None,
    lev_sims: Optional[Dict[str, float]] = None,
) -> None:
    flat = [(sid, sim, st) for _, sid, sim, st, _ in results]
    stats = _compute_stats(flat)
    _nonscored = [(sid, st) for sid, sim, st in flat if sim < 0]
    _crashed_n = sum(1 for _, st in _nonscored if st not in _NONSCORED_STATUSES)
    _excluded_n = len(_nonscored) - _crashed_n
    print()
    print(f"=== BENCHMARK SUMMARY  label={label} ===")
    print(
        f"  Statutes   : {stats['n']} scored  "
        f"crashed: {_crashed_n}  excluded(no oracle/source): {_excluded_n}"
    )
    if oracle_stale_adjusted is not None:
        if oracle_stale_adjusted["mean"] is None:
            print(
                f"  Oracle-aware mean error : n/a"
                f"  (excluded {len(oracle_stale_adjusted['excluded'])} ORACLE_STALE statutes)"
            )
        else:
            print(
                f"  Oracle-aware mean error : {(1 - float(oracle_stale_adjusted['mean'])) * 100:.2f}%"
                f"  (excluded {len(oracle_stale_adjusted['excluded'])} ORACLE_STALE statutes)"
            )
        print(f"  Raw mean error          : {(1 - stats['mean']) * 100:.2f}%")
    else:
        print(f"  Mean error : {(1 - stats['mean']) * 100:.2f}%")
    print(
        f"  Perfect  : {stats['perfect']}  >=99%: {stats['above_99']}  "
        f">=95%: {stats['above_95']}  <90%: {stats['below_90']}"
    )
    if verified:
        total = stats["n"]
        _solved_statuses = {"CLEAN", "ACCEPTABLE"}

        def _vstatus(sid: str) -> str:
            tup = verified.get(sid)
            return tup[0] if tup else ""

        def _is_solved(sid: str, sim: float) -> bool:
            """True when statute is classified as solved AND hasn't regressed >0.1 pp.

            Regression guard only applies to CLEAN/min_pct=100 statutes: for those
            the bench score must stay at 100%.  ACCEPTABLE statutes may have varying
            bench scores by design (oracle divergence accepted), so no guard there.
            """
            tup = verified.get(sid)
            if tup is None:
                return False
            status, min_pct = tup
            if status not in _solved_statuses:
                return False
            # Regression guard: only for CLEAN/perfect (min_pct=100) cases.
            # ACCEPTABLE statutes have varying bench scores by design (oracle
            # divergence accepted), so no guard there — matching the docstring intent.
            if status == "CLEAN" and min_pct == 100 and sim < 0.999:
                return False  # was perfect, now regressed
            return True

        ok_pairs = [(sid, sim) for _, sid, sim, _, _ in results if sim >= 0]
        solved = sum(1 for sid, sim in ok_pairs if _is_solved(sid, sim))
        regressed = sum(
            1 for sid, sim in ok_pairs
            if _vstatus(sid) in _solved_statuses and not _is_solved(sid, sim)
        )
        known_issue = sum(1 for sid, _ in ok_pairs if _vstatus(sid) == "KNOWN_ISSUE")
        unclassified = sum(
            1 for sid, _ in ok_pairs
            if _vstatus(sid) not in _solved_statuses | {"KNOWN_ISSUE"}
        )
        residual = total - solved
        regressed_note = f", {regressed} regressed" if regressed else ""
        # Mean structural error for non-solved statutes
        residual_sims = [
            sim for sid, sim in ok_pairs
            if not _is_solved(sid, sim)
        ]
        residual_mean_err = (1.0 - (sum(residual_sims) / len(residual_sims))) if residual_sims else 0.0
        lev_part = ""
        if lev_sims:
            residual_levs = [lev_sims[sid] for sid, sim in ok_pairs if not _is_solved(sid, sim) and sid in lev_sims]
            if residual_levs:
                lev_part = f"  lev {(1.0 - sum(residual_levs)/len(residual_levs)) * 100:.2f}%"
        print(
            f"  Residual (excl. {solved} solved{regressed_note}):"
            f" mean err {residual_mean_err * 100:.2f}%{lev_part}"
            f"  ({residual} statutes: {known_issue} known-issue, {unclassified} unclassified)"
        )


def _show_worst(
    results: List[Tuple[int, str, float, str, float]],
    top: int,
    lev_sims: Optional[Dict[str, float]] = None,
    verified: Optional[Dict[str, tuple[str, int]]] = None,
) -> None:
    ok = [(sid, sim) for _, sid, sim, st, _ in results if sim >= 0]
    worst = sorted(ok, key=lambda x: x[1])[:top]
    if not worst:
        return
    print(f"\n=== WORST {top} ===")
    for sid, sim in worst:
        lev_part = ""
        if lev_sims is not None:
            lev = lev_sims.get(sid, -1.0)
            lev_part = f"  lev {(1 - lev) * 100:.2f}%" if lev >= 0 else "  lev ---"
        tag = ""
        if verified:
            tup = verified.get(sid)
            if tup:
                vstatus, min_pct = tup
                if vstatus in ("CLEAN", "ACCEPTABLE"):
                    # Regression guard only for CLEAN/perfect — ACCEPTABLE divergences accepted by design
                    if vstatus == "CLEAN" and min_pct == 100 and sim < 0.999:
                        tag = "  [regressed]"
                    else:
                        tag = "  [solved]"
                elif vstatus == "KNOWN_ISSUE":
                    tag = "  [known]"
        print(f"  {sid:12s}  err {(1 - sim) * 100:.2f}%{lev_part}{tag}")


def _show_errors(results: List[Tuple[int, str, float, str, float]]) -> None:
    """Print non-scored statutes at the very end so they're visible in tail output.

    Splits the sim<0 population into two genuinely different categories:
      * EXCLUDED — no oracle/source to compare against (NO_TRUTH, SOURCE_UNAVAILABLE,
        ORACLE_STALE). Not a defect: e.g. a fully-superseded decree whose oracle
        redirects to a successor statute. Excluded from the mean; NOT a crash.
      * CRASHED — a real exception was raised during replay (status carries the
        exception text). These are actionable bugs.
    """
    nonscored = [(sid, st) for _, sid, sim, st, _ in results if sim < 0]
    if not nonscored:
        return
    excluded = [(sid, st) for sid, st in nonscored if st in _NONSCORED_STATUSES]
    crashed = [(sid, st) for sid, st in nonscored if st not in _NONSCORED_STATUSES]
    if excluded:
        print(
            f"\n=== EXCLUDED ({len(excluded)} statute(s), no oracle/source — "
            f"not scored, NOT crashes) ==="
        )
        for sid, status in excluded:
            print(f"  {sid:12s}  {status}")
    if crashed:
        print(f"\n=== ERRORS ({len(crashed)} statute(s) CRASHED — excluded from mean) ===")
        for sid, status in crashed:
            print(f"  {sid:12s}  {status}")
        print("^^^ These statutes crashed during bench and were excluded from scoring.")
        print("    Fix the crash or investigate with: uv run lawvm diff <SID>")


def _show_history(rows: List[Dict]) -> None:
    if not rows:
        print("No benchmark history yet.")
        return
    print(f"{'Timestamp':<22}  {'Label':<10}  {'MeanErr':>8}  {'N':>5}  {'Perfect':>7}  {'>=99%':>6}  {'<90%':>5}")
    print("-" * 80)
    for row in rows:
        mean = float(row.get("mean_score", 0))
        print(
            f"  {row.get('timestamp', '?'):<20}  {row.get('label', '?'):<10}  "
            f"{(1 - mean) * 100:>6.2f}%  {row.get('n_statutes', '?'):>5}  "
            f"{row.get('n_perfect', '?'):>7}  {row.get('n_above_99', '?'):>6}  "
            f"{row.get('n_below_90', '?'):>5}"
        )


def _show_regressions(history: List[Dict]) -> None:
    if len(history) < 2:
        print("Need at least 2 benchmark runs to show regressions.")
        return
    prev = history[-2]
    curr = history[-1]
    print(f"Comparing {prev['label']} → {curr['label']}")

    # Load per-statute data for both
    prev_data = _load_run_by_label(prev["label"])
    curr_data = _load_run_by_label(curr["label"])
    if prev_data is None or curr_data is None:
        print("Per-statute run data not found — only aggregate history available.")
        prev_mean = float(prev["mean_score"])
        curr_mean = float(curr["mean_score"])
        print(
            f"  {prev['label']}: mean_err={(1 - prev_mean) * 100:.2f}%  "
            f"perfect={prev['n_perfect']}  >=99%={prev['n_above_99']}"
        )
        print(
            f"  {curr['label']}: mean_err={(1 - curr_mean) * 100:.2f}%  "
            f"perfect={curr['n_perfect']}  >=99%={curr['n_above_99']}"
        )
        return

    prev_map = dict(prev_data)
    curr_map = dict(curr_data)

    regressions = []
    improvements = []
    for sid in sorted(set(prev_map) | set(curr_map)):
        p = prev_map.get(sid, -1.0)
        c = curr_map.get(sid, -1.0)
        if p < 0 or c < 0:
            continue
        delta = c - p
        if delta < -0.001:
            regressions.append((sid, p, c, delta))
        elif delta > 0.001:
            improvements.append((sid, p, c, delta))

    if regressions:
        print(f"\n  REGRESSIONS ({len(regressions)}):")
        for sid, p, c, d in sorted(regressions, key=lambda x: x[3]):
            print(f"    {sid:12s}  err {(1 - p) * 100:.2f}% → {(1 - c) * 100:.2f}%  ({-d * 100:+.2f}pp)")
    else:
        print("\n  No regressions.")

    if improvements:
        print(f"\n  IMPROVEMENTS ({len(improvements)}):")
        for sid, p, c, d in sorted(improvements, key=lambda x: -x[3])[:10]:
            print(f"    {sid:12s}  err {(1 - p) * 100:.2f}% → {(1 - c) * 100:.2f}%  ({-d * 100:+.2f}pp)")


def _decade(sid: str) -> str:
    """Extract decade string from statute ID (e.g. '1978/38' → '1970s')."""
    try:
        year = int(sid.split("/")[0])
        return f"{(year // 10) * 10}s"
    except (ValueError, IndexError):
        return "????"


def _corpus_stats(corpus: List[Tuple[int, str]]) -> None:
    """Print corpus statistics by decade: N statutes, amendment count distribution."""
    from collections import defaultdict

    by_decade: Dict[str, List[int]] = defaultdict(list)
    for count, sid in corpus:
        by_decade[_decade(sid)].append(count)

    total = len(corpus)
    zero_amend = sum(1 for c, _ in corpus if c == 0)
    print(f"\n=== CORPUS STATS ({total} statutes) ===")
    print(
        f"  0-amendment: {zero_amend} ({zero_amend / total:.0%})  "
        f"≥1-amendment: {total - zero_amend} ({(total - zero_amend) / total:.0%})"
    )
    print(f"\n  {'Decade':<8}  {'N':>5}  {'0-amend':>8}  {'AvgAmend':>10}  {'MaxAmend':>10}")
    print("  " + "-" * 50)
    for decade in sorted(by_decade):
        counts = by_decade[decade]
        n = len(counts)
        zero = sum(1 for c in counts if c == 0)
        avg = sum(counts) / n
        mx = max(counts)
        print(f"  {decade:<8}  {n:>5}  {zero:>7} ({zero * 100 // n:>2}%)  {avg:>10.1f}  {mx:>10}")


def _show_by_decade(data: List[Tuple[str, float]], corpus: Optional[List[Tuple[int, str]]] = None) -> None:
    """Print score breakdown grouped by enactment decade.

    corpus: if provided, add mean amendment count column.
    """
    from collections import defaultdict

    by_decade: Dict[str, List[float]] = defaultdict(list)
    amend_by_decade: Dict[str, List[int]] = defaultdict(list)

    # Build amendment count lookup from corpus if available
    amend_map: Dict[str, int] = {}
    if corpus:
        for count, sid in corpus:
            amend_map[sid] = count

    for sid, sim in data:
        if sim >= 0:
            by_decade[_decade(sid)].append(sim)
            if sid in amend_map:
                amend_by_decade[_decade(sid)].append(amend_map[sid])

    has_amend = bool(amend_map)
    header = f"  {'Decade':<8}  {'N':>5}  {'Mean':>7}  {'>=99%':>6}  {'<90%':>5}  {'<70%':>5}"
    if has_amend:
        header += f"  {'AvgAmend':>9}"
    print("\n=== BY DECADE ===")
    print(header)
    print("  " + "-" * (50 + (10 if has_amend else 0)))
    for decade in sorted(by_decade):
        sims = by_decade[decade]
        n = len(sims)
        mean = sum(sims) / n
        above_99 = sum(1 for s in sims if s >= 0.99)
        below_90 = sum(1 for s in sims if s < 0.90)
        below_70 = sum(1 for s in sims if s < 0.70)
        row = f"  {decade:<8}  {n:>5}  {mean:>7.2%}  {above_99:>6}  {below_90:>5}  {below_70:>5}"
        if has_amend and amend_by_decade[decade]:
            avg_a = sum(amend_by_decade[decade]) / len(amend_by_decade[decade])
            row += f"  {avg_a:>9.1f}"
        print(row)


def _show_run(label: str, top: int = 20, by_decade: bool = False, filter_decade: Optional[str] = None) -> None:
    """Show worst performers from a past labeled run without re-running."""
    data = _load_run_by_label(label)
    if data is None:
        print(f"ERROR: no run found for label '{label}'", file=sys.stderr)
        sys.exit(1)

    lev_data = _load_run_lev_sims(label)

    if filter_decade:
        data = [(sid, sim) for sid, sim in data if _decade(sid) == filter_decade]

    sims = [sim for _, sim in data if sim >= 0]
    if not sims:
        print(f"ERROR: run '{label}' has no valid results", file=sys.stderr)
        sys.exit(1)

    mean = sum(sims) / len(sims)
    perfect = sum(1 for s in sims if s >= 1.0)
    above_99 = sum(1 for s in sims if s >= 0.99)
    above_95 = sum(1 for s in sims if s >= 0.95)
    below_90 = sum(1 for s in sims if s < 0.90)

    print(f"=== BENCHMARK RUN: {label} ===")
    print(f"  Statutes  : {len(sims)}  Mean error: {(1 - mean) * 100:.2f}%")
    print(f"  Perfect: {perfect}  >=99%: {above_99}  >=95%: {above_95}  <90%: {below_90}")

    if by_decade:
        _show_by_decade(data)

    worst = sorted(data, key=lambda x: x[1])[:top]
    print(f"\n=== WORST {top} ===")
    for sid, sim in worst:
        if sim >= 0:
            lev_part = ""
            if lev_data is not None:
                lev = lev_data.get(sid, -1.0)
                lev_part = f"  lev {(1 - lev) * 100:.2f}%" if lev >= 0 else "  lev ---"
            print(f"  {sid:12s}  err {(1 - sim) * 100:.2f}%{lev_part}")
    print()


def _show_compare(label_a: str, label_b: str, top: int = 20) -> None:
    from collections import defaultdict

    data_a = _load_run_by_label(label_a)
    data_b = _load_run_by_label(label_b)

    if data_a is None:
        print(f"ERROR: no run found for label '{label_a}'", file=sys.stderr)
        sys.exit(1)
    if data_b is None:
        print(f"ERROR: no run found for label '{label_b}'", file=sys.stderr)
        sys.exit(1)

    map_a = dict(data_a)
    map_b = dict(data_b)

    raw_diffs = []
    for sid in sorted(set(map_a) | set(map_b)):
        a = map_a.get(sid, -1.0)
        b = map_b.get(sid, -1.0)
        if a < 0 or b < 0:
            continue
        raw_diffs.append((sid, a, b, b - a))

    raw_regressions = [(s, a, b, d) for s, a, b, d in raw_diffs if d < -0.001]
    raw_improvements = [(s, a, b, d) for s, a, b, d in raw_diffs if d > 0.001]
    unchanged = [s for s, a, b, d in raw_diffs if abs(d) <= 0.001]

    shown_regressions = sorted(raw_regressions, key=lambda x: x[3])[:top]
    shown_improvements = sorted(raw_improvements, key=lambda x: -x[3])[:top]

    proof_cache: Dict[str, dict[str, object]] = {}

    def with_proof(rows: List[Tuple[str, float, float, float]]) -> List[Tuple[str, float, float, float, dict[str, object]]]:
        out: List[Tuple[str, float, float, float, dict[str, object]]] = []
        for sid, a, b, d in rows:
            proof = proof_cache.setdefault(sid, _bench_tail_proof_summary(sid))
            out.append((sid, a, b, d, proof))
        return out

    regressions = with_proof(shown_regressions)
    improvements = with_proof(shown_improvements)

    sims_a = [a for _, a, _b, _d in raw_diffs]
    sims_b = [b for _, _a, b, _d in raw_diffs]
    mean_a = sum(sims_a) / len(sims_a) if sims_a else 0.0
    mean_b = sum(sims_b) / len(sims_b) if sims_b else 0.0

    regression_tiers: Dict[str, int] = defaultdict(int)
    improvement_tiers: Dict[str, int] = defaultdict(int)
    for _sid, _a, _b, _d, proof in regressions:
        regression_tiers[str(proof.get("display_primary_tier") or "UNRESOLVED")] += 1
    for _sid, _a, _b, _d, proof in improvements:
        improvement_tiers[str(proof.get("display_primary_tier") or "UNRESOLVED")] += 1

    print(f"Comparing {label_a} vs {label_b}  (N={len(raw_diffs)})")
    print(f"  Mean error: {(1 - mean_a) * 100:.2f}% → {(1 - mean_b) * 100:.2f}%  ({(mean_b - mean_a) * 100:+.2f}pp)")
    print(f"  Regressions: {len(raw_regressions)}  Improvements: {len(raw_improvements)}  Unchanged: {len(unchanged)}")

    if regressions:
        if len(raw_regressions) > len(regressions):
            print(f"  Showing worst {len(regressions)}/{len(raw_regressions)} regressions by error delta")
        print("  Regression display tiers:")
        for tier, n in sorted(regression_tiers.items(), key=lambda item: (-item[1], item[0])):
            print(f"    {tier:<28} {n:4d}")
        print(f"\n  REGRESSIONS ({len(regressions)} shown):")
        for sid, a, b, d, proof in regressions:
            tier = str(proof.get("display_primary_tier") or "UNRESOLVED")
            mixed = "yes" if proof.get("mixed_replay_risk") else "no"
            print(
                f"    {sid:12s}  err {(1 - a) * 100:.2f}% → {(1 - b) * 100:.2f}%  ({-d * 100:+.2f}pp)"
                f"  tier={tier} mixed={mixed}"
            )

    if improvements:
        if len(raw_improvements) > len(improvements):
            print(f"  Showing best {len(improvements)}/{len(raw_improvements)} improvements by error delta")
        print("  Improvement display tiers:")
        for tier, n in sorted(improvement_tiers.items(), key=lambda item: (-item[1], item[0])):
            print(f"    {tier:<28} {n:4d}")
        print(f"\n  IMPROVEMENTS ({len(improvements)} shown):")
        for sid, a, b, d, proof in improvements:
            tier = str(proof.get("display_primary_tier") or "UNRESOLVED")
            mixed = "yes" if proof.get("mixed_replay_risk") else "no"
            print(
                f"    {sid:12s}  err {(1 - a) * 100:.2f}% → {(1 - b) * 100:.2f}%  ({-d * 100:+.2f}pp)"
                f"  tier={tier} mixed={mixed}"
            )

    print()


# ---------------------------------------------------------------------------
# Failure-mode diagnosis (--diagnose, used with --show)
# ---------------------------------------------------------------------------

_KUMOTTU_RE = re.compile(
    r"\bon\s+kumottu\s+[LAP]:ll[äa]|\b\d+\s+momentti\s+on\s+kumottu",
    re.IGNORECASE,
)


def _diag_num_text(el) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _bench_tail_proof_summary(statute_id: str) -> dict[str, object]:
    from lawvm.core.compile_metadata_default import build_default_compile_metadata
    from lawvm.tools.evidence import build_evidence_bundle, _display_primary_tier

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        bundle = build_evidence_bundle(
            statute_id,
            mode="legal_pit",
            include_bisect=True,
            compile_metadata=build_default_compile_metadata(
                jurisdiction="fi",
                source_bundle_hash="sha256:bench-compare",
                build_id="cli.bench.compare.fi",
            ),
        )
    if bundle.get("error"):
        return {
            "primary_proof_tier": "ERROR",
            "display_primary_tier": "ERROR",
            "mixed_replay_risk": False,
        }
    proof_kinds = {
        str(item.get("kind") or "")
        for item in (bundle.get("proof_claims") or [])
        if str(item.get("kind") or "")
    }
    primary = str(bundle.get("primary_proof_tier") or "UNRESOLVED")
    display = _display_primary_tier(primary, proof_kinds)
    strict_reasons = {
        str(reason or "")
        for reason in (bundle.get("strict_fail_reasons") or [])
        if str(reason or "")
    }
    selected_section_kinds = {
        str(item.get("selected_kind") or "")
        for item in (bundle.get("section_claims") or [])
        if str(item.get("selected_kind") or "")
    }
    mixed = (
        primary != "PROVED_REPLAY_BUG"
        and "replay_divergence" in selected_section_kinds
        and bool(strict_reasons & {
            "APPLY.TREE_INVARIANT_VIOLATION",
            "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
            "PARSE.EXTRACTION_FALLBACK",
            "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
            "ELAB.OMISSION_EXPANSION",
            "APPLY.UNCOVERED_BODY_RECOVERY",
        })
    )
    return {
        "primary_proof_tier": primary,
        "display_primary_tier": display,
        "mixed_replay_risk": mixed,
    }


def _diag_norm_num(s: str) -> str:
    return re.sub(r"[\s§.]", "", s).lower()


def _diag_el_text(el) -> str:
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.xml_ingest import xml_element_to_text

    if isinstance(el, IRNode):
        return irnode_to_text(el)
    return xml_element_to_text(el)


def _diag_extract_sections(root) -> Dict[str, Any]:
    from lawvm.tools.section_keys import extract_oracle_sections

    return extract_oracle_sections(root)


def _diag_score(r_el, o_el) -> float:
    r = re.sub(r"[^a-z0-9äöå]", "", _diag_el_text(r_el).lower())
    o = re.sub(r"[^a-z0-9äöå]", "", _diag_el_text(o_el).lower())
    if not r and not o:
        return 1.0
    if not r or not o:
        return 0.0
    return _levenshtein_ratio(r, o)


def _classify_section_pair(r_el, o_el, o_text: str) -> str:
    """Classify a diverging section pair into a failure category."""
    if r_el is None:
        return "KUMOTTU_ORACLE" if _KUMOTTU_RE.search(o_text) else "UNCOVERED_INSERT"
    if o_el is None:
        return "EXTRA_REPLAY"
    # Both present, diverging content
    if _KUMOTTU_RE.search(o_text):
        return "KUMOTTU_ORACLE"
    return "CONTENT_DRIFT"


def _diagnose_run(label: str, top: int, filter_decade: Optional[str]) -> None:
    """Async: diagnose failure modes for worst performers in a saved bench run.

    Uses structural section diffs (same computation as bench score) to classify
    divergences by semantic event type.
    """
    from collections import defaultdict
    from lawvm.tools.structural_review import (
        compute_statute_section_diffs,
        _sections_with_diffs,
    )

    data = _load_run_by_label(label)
    if data is None:
        print(f"ERROR: no run found for label '{label}'", file=sys.stderr)
        sys.exit(1)

    if filter_decade:
        data = [(sid, sim) for sid, sim in data if _decade(sid) == filter_decade]

    worst = sorted([(sid, sim) for sid, sim in data if sim >= 0], key=lambda x: x[1])[:top]
    print(f"Diagnosing worst {len(worst)} statutes from run '{label}'...\n")

    agg: Counter[str] = Counter()
    proof_tier_counts: Dict[str, int] = defaultdict(int)
    rows = []

    for sid, sim in worst:
        proof = _bench_tail_proof_summary(sid)
        proof_tier_counts[str(proof.get("display_primary_tier") or "UNRESOLVED")] += 1
        try:
            sections, oracle_absent = compute_statute_section_diffs(
                sid,
                oracle_selector_mode="bench_comparable",
            )
        except (NameError, TypeError, AttributeError):
            raise
        except Exception:
            rows.append((sid, sim, Counter(), "replay_error", proof))
            continue

        if oracle_absent:
            rows.append((sid, sim, Counter({"ORACLE_ABSENT": 1}), "", proof))
            agg["ORACLE_ABSENT"] += 1
            continue

        non_editorial = {
            k: v for k, v in sections.items()
            if v.get("semantic_diff", {}).get("kind") != "editorial_only"
        }
        counts: Counter[str] = Counter()
        for _sec_key, _sd, events in _sections_with_diffs({"sections": non_editorial}):
            for event in events:
                counts[event.get("kind", "unknown")] += 1
        agg.update(counts)
        rows.append((sid, sim, counts, "", proof))

    # Event display order — most actionable first
    _DIAG_CATS = [
        "unit_missing_left",   # section only in oracle → replay defect or source gap
        "unit_missing_right",  # section only in replay → extra/spurious in replay
        "wording_text_changed",
        "heading_text_changed",
        "facet_removed",
        "facet_added",
        "ORACLE_ABSENT",
    ]

    for sid, sim, counts, note, proof in rows:
        parts = [f"{c}={counts[c]}" for c in _DIAG_CATS if counts.get(c)]
        # also show any event kinds not in _DIAG_CATS
        extra = {k: v for k, v in counts.items() if k not in _DIAG_CATS and k != "PERFECT"}
        parts.extend(f"{k}={v}" for k, v in sorted(extra.items()))
        note_str = f"  [{note}]" if note else ""
        tier = str(proof.get("display_primary_tier") or "UNRESOLVED")
        mixed = "yes" if proof.get("mixed_replay_risk") else "no"
        print(f"  {sid:12s}  {sim:.1%}  {', '.join(parts) or '(no events)'}  tier={tier} mixed={mixed}{note_str}")

    total = sum(agg.values())
    print(f"\nAggregate divergences: {total} across {len(worst)} statutes")
    print("Display tiers:")
    for tier, n in sorted(proof_tier_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {tier:<28} {n:4d}")
    for cat in _DIAG_CATS:
        n = agg.get(cat, 0)
        if n:
            pct = n / total * 100 if total else 0
            print(f"  {cat:<30} {n:4d}  ({pct:.0f}%)")
    # Also show any extra event kinds
    for cat, n in sorted(agg.items()):
        if cat not in _DIAG_CATS and n:
            pct = n / total * 100 if total else 0
            print(f"  {cat:<30} {n:4d}  ({pct:.0f}%)")
    print()


# ---------------------------------------------------------------------------
# HTML oracle freshness check (--html-summary)
# ---------------------------------------------------------------------------


def _html_label_summary(sids_and_scores: List[Tuple[str, float]]) -> None:
    """Report HTML oracle freshness impact on bench scores.

    Compares section counts between the corpus oracle and the live HTML oracle
    to identify statutes where the ZIP is stale (HTML has significantly more
    sections).  Reports mean error split by fresh vs stale oracles so the
    caller can read off an adjusted accuracy figure.

    No replay is performed — only ZIP and HTML metadata are used.
    """
    try:
        from lawvm.finland.finlex_html import html_section_labels
    except ImportError:
        print("\n  HTML summary: finlex_html module not available — skipped.")
        return

    # HTML data now lives in the main farchive DB — no separate cache file needed.

    stale_count = 0
    stale_error_sum = 0.0
    fresh_count = 0
    fresh_error_sum = 0.0
    skipped = 0

    for sid, score in sids_and_scores:
        if score < 0:
            skipped += 1
            continue

        try:
            year, num = sid.split("/", 1)
        except ValueError:
            skipped += 1
            continue

        # HTML section count from cache (cache_only: don't fetch live)
        html_labels = html_section_labels(year, num, max_age_hours=float("inf"))
        if html_labels is None:
            skipped += 1
            continue

        # corpus oracle section count
        oracle_data = get_ground_truth_bytes(sid, selector=_BENCH_CONSOLIDATED_SELECTOR)
        if oracle_data is None:
            skipped += 1
            continue
        zip_count = oracle_data.count(b"<section")
        html_count = len(html_labels)

        error = 1.0 - score
        if html_count > zip_count + 1:  # HTML has significantly more sections
            stale_count += 1
            stale_error_sum += error
        else:
            fresh_count += 1
            fresh_error_sum += error

    total = stale_count + fresh_count
    if total == 0:
        print(f"\n  HTML summary: no comparable statutes found (skipped={skipped}).")
        return

    print(f"\n  HTML oracle freshness check ({total} statutes, {skipped} skipped):")
    if fresh_count:
        mean_fresh_err = fresh_error_sum / fresh_count
        print(
            f"    Fresh oracles:  {fresh_count} ({100 * fresh_count / total:.0f}%)"
            f"  mean error {mean_fresh_err * 100:.2f}%"
        )
    if stale_count:
        mean_stale_err = stale_error_sum / stale_count
        print(
            f"    Stale oracles:  {stale_count} ({100 * stale_count / total:.0f}%)"
            f"  mean error {mean_stale_err * 100:.2f}%"
        )
        if fresh_count:
            adjusted_error = fresh_error_sum / fresh_count
            print(
                f"    Adjusted error (fresh only): {adjusted_error * 100:.2f}%"
                f"  ({100 * (1.0 - adjusted_error):.2f}% accuracy)"
            )


# ---------------------------------------------------------------------------
# Warm oracle (pre-fetch API PITs for statutes lacking PIT versions)
# ---------------------------------------------------------------------------


def _warm_oracle(sids: list[str], *, force: bool = False) -> int:
    """Pre-fetch missing oracle cache entries before parallel benchmarking.

    Only runs when TransparentCorpusStore is active. The warm pass operates on
    the SQLite/API path only and never relies on corpus fallback state.

    Returns the number of statutes successfully cached during this pass.
    """
    from lawvm.corpus_store import get_corpus_store

    cs = get_corpus_store()

    try:
        from lawvm.finland.transparent_store import TransparentCorpusStore
    except ImportError:
        print("  --warm-oracle: TransparentCorpusStore module not available — skipped.")
        return 0

    if not isinstance(cs, TransparentCorpusStore):
        print(
            "  --warm-oracle requires TransparentCorpusStore "
            "(ensure data/finlex.farchive exists)"
        )
        return 0

    # Use the oracle path index for a fast existence check instead of
    # reading each oracle XML individually (which is extremely slow for
    # large corpora — each read_oracle() parses the full XML).
    try:
        oracle_index = cs.oracle_path_index()
        missing = [s for s in sids if s not in oracle_index]
    except (AttributeError, Exception):
        # Fallback: check each statute individually (slow path)
        missing = [s for s in sids if cs.read_oracle(s) is None]

    if not missing:
        print(f"  All {len(sids)} statutes already have cached oracle entries.")
        return 0

    print(f"  Warming {len(missing)} statutes with missing oracle cache entries...")
    fetched = 0
    for i, sid in enumerate(missing, 1):
        try:
            data = cs.refresh(sid, force=force)
            if data is not None:
                fetched += 1
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        if i % 20 == 0 or i == len(missing):
            print(f"    [{i}/{len(missing)}] {fetched} fetched")

    return fetched


def _warm_sources(sids: list[str]) -> int:
    """Pre-fetch missing source/amendment XMLs referenced by the bench corpus.

    This serial pass prevents worker processes from fan-out fetching source
    acts or amendment acts during replay. For source XMLs, a one-time corpus
    bootstrap into SQLite is acceptable because the result is persisted and
    workers thereafter read only from SQLite.
    """
    from lawvm.corpus_store import get_corpus_store
    from lawvm.corpus_store import statute_url
    from lawvm.finland.amendment_selection import amendment_children_by_parent as _amendment_children_by_parent

    cs = get_corpus_store()

    try:
        from lawvm.finland.transparent_store import TransparentCorpusStore
    except ImportError:
        print("  source preflight: TransparentCorpusStore module not available — skipped.")
        return 0

    if not isinstance(cs, TransparentCorpusStore):
        return 0

    children = _amendment_children_by_parent()
    required_ids = set(sids)
    for sid in sids:
        required_ids.update(children.get(sid, ()))

    missing: list[str] = []
    for sid in sorted(required_ids):
        url = statute_url(sid)
        # Check primary archive (source/amendment XMLs live under finlex://sd/... locators)
        _get = getattr(cs._archive, "get", None) or getattr(cs._archive, "get_latest", None)
        if _get is not None and _get(url) is not None:
            continue
        missing.append(sid)

    if not missing:
        print(f"  All {len(required_ids)} source/amendment XMLs already exist in cache.")
        return 0

    print(f"  Warming {len(missing)} missing source/amendment XMLs...")
    fetched = 0
    for i, sid in enumerate(missing, 1):
        try:
            data = cs.refresh_source(sid)
            if data is not None:
                fetched += 1
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        if i % 20 == 0 or i == len(missing):
            print(f"    [{i}/{len(missing)}] {fetched} fetched")

    return fetched


def _fi_bench_worker_count(args: Any) -> int:
    """Return the Finland bench worker count requested by CLI args."""
    explicit = getattr(args, "parallel", None)
    if explicit is None:
        return max(1, min(_FI_BENCH_DEFAULT_MAX_WORKERS, os.cpu_count() or 1))
    if explicit < 1:
        print("ERROR: --parallel must be a positive integer", file=sys.stderr)
        raise SystemExit(2)
    return explicit


def _format_bench_run_banner(
    *,
    statute_count: int,
    label: str,
    mode: str,
    workers: int,
    section_score_mode: bool,
    fast_mode: bool,
    text_scores: bool,
    diagnostic_replay: bool,
) -> str:
    return (
        f"Running benchmark: {statute_count} statutes  label={label}  "
        f"mode={mode}  workers={workers}"
        + ("  [+section-score]" if section_score_mode else "")
        + ("  [fast]" if fast_mode else "")
        + ("  [no-text-scores]" if not text_scores else "")
        + ("  [diagnostic-replay]" if diagnostic_replay else "  [quiet-replay]")
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(args) -> None:
    history_only = getattr(args, "history", False)
    compare = getattr(args, "compare", None)  # list of two labels
    regressions_only = getattr(args, "regressions", False)
    show_label = getattr(args, "show", None)
    label = getattr(args, "label", None)
    corpus_path = getattr(args, "corpus", None)
    top = getattr(args, "top", 20)
    by_decade = getattr(args, "by_decade", False)
    filter_decade = getattr(args, "filter_decade", None)
    diagnose = getattr(args, "diagnose", False)
    diagnostic_replay = getattr(args, "diagnostic_replay", False)

    # --show LABEL: show past run's worst without re-running
    if show_label:
        if diagnose:
            _diagnose_run(show_label, top, filter_decade)
        else:
            _show_run(show_label, top, by_decade=by_decade, filter_decade=filter_decade)
        return

    # --history: just show history, no run needed
    if history_only:
        history = _load_history()
        _show_history(history)
        return

    # --regressions: compare last two runs
    if regressions_only:
        history = _load_history()
        _show_regressions(history)
        return

    # --compare A B
    if compare:
        if len(compare) != 2:
            print("ERROR: --compare requires exactly two labels", file=sys.stderr)
            sys.exit(1)
        _show_compare(compare[0], compare[1], top=top)
        return

    # Run benchmark
    if corpus_path is None:
        corpus_path = _default_corpus_path()
    if not os.path.exists(corpus_path):
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    corpus = _load_corpus(corpus_path)
    if not corpus:
        print(f"ERROR: corpus file empty or unparseable: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    filter_live = getattr(args, "filter_live", False)
    filter_repealed = getattr(args, "filter_repealed", False)
    filter_empty = getattr(args, "filter_empty", False)
    if filter_live or filter_repealed or filter_empty:
        corpus = _apply_corpus_filters(corpus, filter_live, filter_repealed, filter_empty)
        if not corpus:
            print("ERROR: no statutes remain after filtering.", file=sys.stderr)
            sys.exit(1)

    filter_zero_amend = getattr(args, "filter_zero_amend", False)
    filter_nonzero_amend = getattr(args, "filter_nonzero_amend", False)
    if filter_zero_amend:
        corpus = _filter_by_amend_count(corpus, max_amend=0)
        if not corpus:
            print("ERROR: no 0-amendment statutes in corpus.", file=sys.stderr)
            sys.exit(1)
    elif filter_nonzero_amend:
        corpus = _filter_by_amend_count(corpus, min_amend=1)
        if not corpus:
            print("ERROR: no statutes with amendments in corpus.", file=sys.stderr)
            sys.exit(1)

    if filter_decade:
        corpus = _filter_by_decade(corpus, filter_decade)
        if not corpus:
            print(f"ERROR: no statutes in decade {filter_decade!r}.", file=sys.stderr)
            sys.exit(1)

    if getattr(args, "corpus_stats", False):
        _corpus_stats(corpus)
        return

    statute_filter = getattr(args, "statute", None)
    if statute_filter:
        corpus = [(c, sid) for c, sid in corpus if sid == statute_filter]
        if not corpus:
            print(f"ERROR: statute {statute_filter!r} not found in corpus.", file=sys.stderr)
            sys.exit(1)

    limit_n = getattr(args, "limit", None)
    if limit_n is not None:
        corpus = corpus[:limit_n]

    all_sids = [sid for _, sid in corpus]

    # Source/oracle preflight is only needed when running against a live API
    # (TransparentCorpusStore with network). For local farchive benches,
    # assume all data is present and skip the expensive existence checks.
    if getattr(args, "warm_oracle", False):
        print(f"Source cache preflight ({len(all_sids)} statutes)...")
        n_source_fetched = _warm_sources(all_sids)
        if n_source_fetched:
            print(f"  Source cache preflight done: {n_source_fetched} XMLs fetched from API.")
            print()

    if getattr(args, "warm_oracle", False):
        print(f"Warm-oracle pass ({len(all_sids)} statutes)...")
        n_fetched = _warm_oracle(all_sids, force=True)
        print(f"  Warm-oracle done: {n_fetched} statutes fetched from API.")
        print()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    if label is None:
        label = f"run_{timestamp.replace(':', '').replace('-', '')[:13]}"

    section_score_mode = getattr(args, "section_score", False)
    fast_mode = getattr(args, "fast", False)
    text_scores = not bool(getattr(args, "no_text_scores", False))
    no_save = bool(getattr(args, "no_save", False))
    bench_mode = getattr(args, "mode", "official_consolidation") or "official_consolidation"
    oracle_stale_headline = getattr(args, "oracle_aware_headline", False)
    workers = _fi_bench_worker_count(args)

    # Pin one commencement reference date for the whole run so oracle selection
    # is reproducible: every comparability decision in this run uses the same
    # ``as_of`` even if the wall clock crosses midnight mid-run, and a
    # future-dated artifact rejected today cannot become silently accepted
    # tomorrow with no code/data change. Forked workers inherit this pinned
    # module-global via copy-on-write (the pool is created after this line).
    # Snapshot from the same UTC clock as the run timestamp.
    selection_as_of = datetime.now(timezone.utc).date()
    pin_selection_as_of(selection_as_of)

    print(
        _format_bench_run_banner(
            statute_count=len(corpus),
            label=label,
            mode=bench_mode,
            workers=workers,
            section_score_mode=section_score_mode,
            fast_mode=fast_mode,
            text_scores=text_scores,
            diagnostic_replay=diagnostic_replay,
        )
    )
    print()

    section_results: Optional[List[Tuple[int, str, float, float, str, float]]] = None
    lev_sims: Optional[Dict[str, float]] = None
    diagnostic_summaries: Dict[str, str] = {}
    diagnostic_counts: Dict[str, Dict[str, int]] = {}
    if section_score_mode:
        section_results = _run_benchmark_section(
            corpus,
            verbose=True,
            workers=workers,
            diagnostic_replay=diagnostic_replay,
            mode=bench_mode,
            diagnostic_summaries_out=diagnostic_summaries,
            diagnostic_counts_out=diagnostic_counts,
        )
        # Extract standard (text) results from section_results for summary/history
        results = [
            (count, sid, text_sim, status, elapsed)
            for count, sid, text_sim, _section_sim, status, elapsed in section_results
        ]
    else:
        results, lev_sims = _run_benchmark(
            corpus,
            verbose=True,
            workers=workers,
            diagnostic_replay=diagnostic_replay,
            mode=bench_mode,
            fast=fast_mode,
            text_scores=text_scores,
            diagnostic_summaries_out=diagnostic_summaries,
            diagnostic_counts_out=diagnostic_counts,
        )

    if diagnostic_counts:
        print_bench_diagnostic_tier_rollup(merge_diagnostic_counter_dicts(list(diagnostic_counts.values())))

    flat = [(sid, sim, st) for _, sid, sim, st, _ in results]
    stats = _compute_stats(flat)

    oracle_adjusted_summary = None
    if oracle_stale_headline:
        oracle_adjusted_summary = _oracle_stale_adjusted_stats(
            results,
            workers=workers,
            mode=bench_mode,
            diagnostic_counts=diagnostic_counts,
        )

    verified = _load_verified_statutes()
    _show_summary(results, label, oracle_stale_adjusted=oracle_adjusted_summary, verified=verified or None, lev_sims=lev_sims)
    if _fi_bench_unified_enabled():
        # Opt-in shared cross-jurisdiction summary (LAWVM_BENCH_UNIFIED). Reuses
        # the per-statute numbers already computed above; default output above is
        # unchanged. When section scoring ran, fold in the structural axis so the
        # FI worst-of headline binds on the worse of section/text divergence.
        section_sims: Optional[Dict[str, float]] = None
        if section_score_mode and section_results is not None:
            section_sims = {
                sid: section_sim
                for _count, sid, _text_sim, section_sim, _status, _elapsed in section_results
            }
        _show_unified_summary_fi(flat, label, section_sims=section_sims)
    if by_decade:
        _show_by_decade([(sid, sim) for _, sid, sim, _, _ in results], corpus=corpus)
    _show_worst(results, top, lev_sims=lev_sims, verified=verified or None)

    # Section-level summary
    if section_score_mode and section_results is not None:
        sec_sims = [
            section_sim for _, _sid, _text_sim, section_sim, _status, _elapsed in section_results if section_sim >= 0
        ]
        txt_sims = [text_sim for _, _sid, text_sim, _section_sim, _status, _elapsed in section_results if text_sim >= 0]
        if sec_sims and txt_sims:
            mean_sec = sum(sec_sims) / len(sec_sims)
            mean_txt = sum(txt_sims) / len(txt_sims)
            delta_pp = (mean_sec - mean_txt) * 100
            print()
            print(f"Section-level accuracy: {mean_sec * 100:.2f}%  (mean per-section similarity)")
            print(f"Full-text accuracy:     {mean_txt * 100:.2f}%  (standard Levenshtein)")
            print(f"Delta:                  {delta_pp:+.2f}pp")

    # Show lev_sims aggregate for non-section-score mode
    if lev_sims:
        valid_lev = [v for v in lev_sims.values() if v >= 0]
        if valid_lev:
            mean_lev = sum(valid_lev) / len(valid_lev)
            mean_struct = stats["mean"]
            print()
            print(f"Structural accuracy: {mean_struct * 100:.2f}%  (section-level structural diff, primary)")
            print(f"Levenshtein:         {mean_lev * 100:.2f}%  (full-text, secondary — tree-structure sanity)")

    if no_save:
        print("\nRun not saved (--no-save)")
    else:
        run_path = _save_run(
            results,
            label,
            timestamp,
            section_results=section_results,
            lev_sims=lev_sims,
            diagnostic_summaries=diagnostic_summaries,
        )
        _append_history(timestamp, label, stats)
        evidence_report_path = _write_bench_evidence_surface(
            run_path=run_path,
            label=label,
            timestamp=timestamp,
            mode=bench_mode,
            corpus_path=corpus_path,
            stats=stats,
            results=results,
            workers=workers,
            section_score=section_score_mode,
            fast_mode=fast_mode,
            diagnostic_replay=diagnostic_replay,
            lev_sims=lev_sims,
            diagnostic_summaries=diagnostic_summaries,
            oracle_stale_adjusted=oracle_adjusted_summary,
            selection_as_of=selection_as_of.isoformat(),
        )

        diagnostics_sidecar_path = _save_bench_diagnostic_sidecar(
            run_path=run_path,
            label=label,
            results=results,
            diagnostic_counts=diagnostic_counts,
        )

        print(f"\nRun saved: {run_path}")
        print(f"History  : {_history_path()}")
        print(f"Evidence : {evidence_report_path}")
        if diagnostics_sidecar_path is not None:
            print(f"Diagnostics: {diagnostics_sidecar_path}")

    # Show errors last — the most visible position for tail output
    _show_errors(results)

    # Optional HTML oracle freshness check
    if getattr(args, "html_summary", False):
        sids_and_scores = [(sid, sim) for _, sid, sim, _, _ in results]
        _html_label_summary(sids_and_scores)


def register_cli(sub: Any, _j_parent: Any) -> None:
    """Register the 'bench' subcommand onto an argparse subparsers object."""
    _P = [_j_parent]
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
            "parallel workers (FI default: min(16, cpu_count); UK/EE use "
            "jurisdiction-specific defaults); per-worker peak RSS ~860 MB after source-root "
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
