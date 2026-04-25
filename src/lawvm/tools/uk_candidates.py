"""lawvm uk-candidates -- candidate-aware UK frontier triage from a saved bench run."""
from __future__ import annotations

import contextlib
import io
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


def _primary_frontier_score(result, *, score_mode: str = "auto") -> float:  # noqa: ANN001
    if score_mode == "replay":
        if result.replay_score >= 0.0:
            return result.replay_score
        if result.replay_commencement_score >= 0.0:
            return result.replay_commencement_score
        if result.commencement_score >= 0.0:
            return result.commencement_score
        return result.score
    if score_mode == "replay_commencement":
        if result.replay_commencement_score >= 0.0:
            return result.replay_commencement_score
        if result.replay_score >= 0.0:
            return result.replay_score
        if result.commencement_score >= 0.0:
            return result.commencement_score
        return result.score
    if (
        result.replay_commencement_score >= 0.0
        and int(getattr(result, "n_commenced_eids", 0) or 0) > 0
    ):
        return result.replay_commencement_score
    if result.replay_score >= 0.0:
        return result.replay_score
    if result.commencement_score >= 0.0:
        return result.commencement_score
    return result.score


def _effective_comparison_class(result) -> str:  # noqa: ANN001
    from lawvm.uk_legislation.source_adjudication import classify_uk_bench_comparison

    comparison_class = str(getattr(result, "comparison_class", "") or "").strip()
    if comparison_class:
        return comparison_class
    return classify_uk_bench_comparison(
        n_enacted_eids=int(getattr(result, "n_enacted_eids", 0) or 0),
        n_oracle_eids=int(getattr(result, "n_oracle_eids", 0) or 0),
        n_effects=int(getattr(result, "n_effects", 0) or 0),
        raw_score=float(getattr(result, "score", 0.0) or 0.0),
    )


def _effective_core_benchmark(result) -> bool:  # noqa: ANN001
    from lawvm.uk_legislation.source_adjudication import is_core_uk_comparison

    return is_core_uk_comparison(_effective_comparison_class(result))


def _matches_filters(  # noqa: ANN001
    result,
    *,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
) -> bool:
    year = int(getattr(result, "year", 0) or 0)
    act_type = str(getattr(result, "act_type", "") or "")
    if min_year is not None and year < min_year:
        return False
    if max_year is not None and year > max_year:
        return False
    if types is not None and act_type not in types:
        return False
    return True


def _filtered_frontier(  # noqa: ANN001
    results,
    *,
    top: int,
    score_mode: str,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
):
    frontier = [
        r for r in results
        if r.status == "OK"
        and _effective_core_benchmark(r)
        and _primary_frontier_score(r, score_mode=score_mode) < 1.0
        and _matches_filters(r, min_year=min_year, max_year=max_year, types=types)
    ]
    frontier.sort(key=lambda r: _primary_frontier_score(r, score_mode=score_mode))
    return frontier[:top]


def _effect_overlaps_residual(
    resolver_eids: tuple[str, ...],
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> bool:
    residual = {eid.lower() for eid in only_in_replayed} | {
        eid.lower() for eid in only_in_oracle
    }
    for resolver_eid in resolver_eids:
        norm = resolver_eid.lower()
        if not norm:
            continue
        if norm in residual:
            return True
        prefix = norm + "-"
        if any(other.startswith(prefix) for other in residual):
            return True
    return False


def _eid_branch_root(eid: str) -> str:
    norm = str(eid or "").strip().lower()
    if not norm:
        return ""
    if norm == "schedule":
        return norm
    if norm.startswith("schedule-"):
        parts = norm.split("-")
        if len(parts) >= 2 and parts[1] not in {
            "part",
            "chapter",
            "crossheading",
            "paragraph",
            "subparagraph",
            "item",
            "point",
        }:
            return "-".join(parts[:2])
        return "schedule"
    for prefix in (
        "section-",
        "article-",
        "rule-",
        "regulation-",
        "chapter-",
        "part-",
        "division-",
        "recital-",
    ):
        if norm.startswith(prefix):
            parts = norm.split("-")
            if len(parts) >= 2:
                return "-".join(parts[:2])
    if norm.startswith(("crossheading-", "p1group")):
        return norm
    return norm


def _collect_residual_roots(
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> set[str]:
    roots: set[str] = set()
    for eid in only_in_replayed | only_in_oracle:
        root = _eid_branch_root(eid)
        if root:
            roots.add(root)
    return roots


def _candidate_root_hits(
    resolver_eids: tuple[str, ...],
    *,
    residual_roots: set[str],
) -> set[str]:
    hits: set[str] = set()
    for resolver_eid in resolver_eids:
        root = _eid_branch_root(resolver_eid)
        if root and root in residual_roots:
            hits.add(root)
    return hits


def _frontier_status(
    *,
    candidate_count: int,
    residual_candidate_count: int,
    residual_root_count: int = 0,
    defeated_residual_root_count: int = 0,
) -> str:
    if residual_candidate_count > 0:
        return "real residual frontier"
    if residual_root_count > 0 and defeated_residual_root_count == residual_root_count:
        return "residual branches defeated by no candidate overlap"
    if candidate_count == 0:
        return "classification-heavy"
    return "candidate-clean after residual overlap"


def _summarize_effect_inventory(
    effect_summaries,
):
    source_counts: Counter[str] = Counter()
    compare_counts: Counter[str] = Counter()
    candidate_count = 0
    candidate_ops = 0
    candidate_summaries = []
    for summary in effect_summaries:
        if summary.source_pathology:
            source_counts[summary.source_pathology] += 1
        if summary.compare_shape:
            compare_counts[summary.compare_shape] += 1
        if summary.candidate:
            candidate_count += 1
            candidate_ops += summary.n_ops
            candidate_summaries.append(summary)
    return {
        "source_counts": source_counts,
        "compare_counts": compare_counts,
        "candidate_count": candidate_count,
        "candidate_ops": candidate_ops,
        "candidate_summaries": candidate_summaries,
    }


def _residual_candidate_inventory(
    candidate_summaries,
    *,
    residual_roots: set[str],
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> dict[str, Any]:
    residual_candidate_count = 0
    residual_candidate_ops = 0
    residual_root_hits: set[str] = set()
    for summary in candidate_summaries:
        residual_root_hits.update(
            _candidate_root_hits(
                summary.resolver_eids,
                residual_roots=residual_roots,
            )
        )
        if _effect_overlaps_residual(
            summary.resolver_eids,
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
        ):
            residual_candidate_count += 1
            residual_candidate_ops += summary.n_ops
    return {
        "residual_candidate_count": residual_candidate_count,
        "residual_candidate_ops": residual_candidate_ops,
        "residual_root_hits": residual_root_hits,
    }


def main(args: "argparse.Namespace") -> None:
    from lawvm.tools.uk_bench import _load_run

    label: str = args.label
    top: int = getattr(args, "top", 15)
    fast: bool = bool(getattr(args, "fast", False))
    score_mode: str = str(getattr(args, "score_mode", "auto") or "auto")
    residual_only: bool = bool(getattr(args, "residual_only", False))
    min_year: int | None = getattr(args, "min_year", None)
    max_year: int | None = getattr(args, "max_year", None)
    type_args = getattr(args, "types", None)
    types = {str(t).strip() for t in type_args} if type_args else None
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB

    if not fast and not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    results = _load_run(label)
    frontier = _filtered_frontier(
        results,
        top=top,
        score_mode=score_mode,
        min_year=min_year,
        max_year=max_year,
        types=types,
    )

    print(f"=== UK Candidates: {label} ===")
    print(f"Top rows inspected: {len(frontier)}")
    if min_year is not None or max_year is not None or types is not None:
        parts: list[str] = []
        if min_year is not None:
            parts.append(f"min_year={min_year}")
        if max_year is not None:
            parts.append(f"max_year={max_year}")
        if types is not None:
            parts.append(f"types={','.join(sorted(types))}")
        parts.append(f"score_mode={score_mode}")
        print(f"Filters: {' '.join(parts)}")
    else:
        print(f"Score mode: {score_mode}")
    if not frontier:
        return
    print()

    if fast:
        if residual_only and db_path.exists():
            from farchive import Farchive
            from lawvm.tools.uk_effects import (
                build_uk_effect_summary_context,
                summarize_uk_effect,
            )
            from lawvm.tools.uk_effect import _collect_statute_eids
            from lawvm.uk_legislation.source_adjudication import (
                normalize_uk_replay_compare_eids,
            )
            from lawvm.uk_legislation.uk_amendment_replay import (
                UKReplayPipeline,
                load_effects_for_statute_from_archive,
            )

            with Farchive(db_path) as archive:
                for r in frontier:
                    effects = load_effects_for_statute_from_archive(r.statute_id, archive)
                    context = build_uk_effect_summary_context(r.statute_id, archive=archive)
                    effect_summaries = [
                        summarize_uk_effect(effect, archive=archive, context=context)
                        for effect in effects
                        if effect.applied
                    ]
                    inventory = _summarize_effect_inventory(effect_summaries)
                    source_counts = inventory["source_counts"]
                    compare_counts = inventory["compare_counts"]
                    candidate_count = int(inventory["candidate_count"])
                    candidate_ops = int(inventory["candidate_ops"])
                    candidate_summaries = list(inventory["candidate_summaries"])
                    if candidate_count <= 0:
                        continue
                    replay_only: set[str] = set()
                    oracle_only: set[str] = set()
                    if context.enacted_ir is not None and context.oracle_eids:
                        pipeline = UKReplayPipeline(_REPO_ROOT)
                        with contextlib.redirect_stdout(io.StringIO()):
                            replayed_ir = pipeline.apply_ops(
                                context.enacted_ir,
                                pipeline.compile_ops_for_statute(r.statute_id, archive=archive),
                                eid_map=context.oracle_eid_map,
                                text_map=context.oracle_text_map,
                            )
                        replayed_eids = _collect_statute_eids(replayed_ir)
                        replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                            replayed_eids,
                            context.oracle_eids,
                        )
                        replay_only = replay_compare_eids - oracle_compare_eids
                        oracle_only = oracle_compare_eids - replay_compare_eids
                    residual_roots = _collect_residual_roots(
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    residual_inventory = _residual_candidate_inventory(
                        candidate_summaries,
                        residual_roots=residual_roots,
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    residual_candidate_count = int(residual_inventory["residual_candidate_count"])
                    residual_candidate_ops = int(residual_inventory["residual_candidate_ops"])
                    residual_root_hits = set(residual_inventory["residual_root_hits"])
                    defeated_residual_roots = residual_roots - residual_root_hits

                    if residual_candidate_count <= 0:
                        continue

                    score = _primary_frontier_score(r, score_mode=score_mode)
                    comparison_class = _effective_comparison_class(r)
                    status = _frontier_status(
                        candidate_count=candidate_count,
                        residual_candidate_count=residual_candidate_count,
                        residual_root_count=len(residual_roots),
                        defeated_residual_root_count=len(defeated_residual_roots),
                    )
                    print(
                        f"{r.statute_id:<30} frontier={score:.1%} "
                        f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                        f"effects={r.n_effects:4d} candidate_effects={candidate_count:3d} "
                        f"candidate_ops={candidate_ops:4d} "
                        f"residual_candidate_effects={residual_candidate_count:3d} "
                        f"residual_candidate_ops={residual_candidate_ops:4d}"
                    )
                    print(f"  class:    {comparison_class}")
                    if source_counts:
                        top_source = ", ".join(
                            f"{k}={v}" for k, v in source_counts.most_common(3)
                        )
                        print(f"  source:   {top_source}")
                    else:
                        print("  source:   (none)")
                    if compare_counts:
                        top_compare = ", ".join(
                            f"{k}={v}" for k, v in compare_counts.most_common(3)
                        )
                        print(f"  compare:  {top_compare}")
                    else:
                        print("  compare:  (none)")
                    if residual_roots:
                        print(
                            f"  claims:   residual_roots={len(residual_roots):3d} "
                            f"backed_roots={len(residual_root_hits):3d} "
                            f"defeated_roots={len(defeated_residual_roots):3d}"
                        )
                    print(f"  status:   {status}")
                    print()
            return

        for r in frontier:
            score = _primary_frontier_score(r, score_mode=score_mode)
            comparison_class = _effective_comparison_class(r)
            print(
                f"{r.statute_id:<30} frontier={score:.1%} "
                f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                f"effects={r.n_effects:4d}"
            )
            print(f"  class:    {comparison_class}")
            print("  source:   (skipped --fast)")
            print("  compare:  (skipped --fast)")
            print("  status:   frontier prefilter only")
            print()
        return

    from farchive import Farchive
    from lawvm.tools.uk_effects import (
        build_uk_effect_summary_context,
        summarize_uk_effect,
    )
    from lawvm.tools.uk_effect import _collect_statute_eids
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayPipeline,
        load_effects_for_statute_from_archive,
    )

    with Farchive(db_path) as archive:
        for r in frontier:
            effects = load_effects_for_statute_from_archive(r.statute_id, archive)
            context = build_uk_effect_summary_context(r.statute_id, archive=archive)
            effect_summaries = [
                summarize_uk_effect(effect, archive=archive, context=context)
                for effect in effects
                if effect.applied
            ]
            inventory = _summarize_effect_inventory(effect_summaries)
            replay_only: set[str] = set()
            oracle_only: set[str] = set()
            if context.enacted_ir is not None and context.oracle_eids:
                pipeline = UKReplayPipeline(_REPO_ROOT)
                ops = pipeline.compile_ops_for_statute(r.statute_id, archive=archive)
                with contextlib.redirect_stdout(io.StringIO()):
                    replayed_ir = pipeline.apply_ops(
                        context.enacted_ir,
                        ops,
                        eid_map=context.oracle_eid_map,
                        text_map=context.oracle_text_map,
                    )
                replayed_eids = _collect_statute_eids(replayed_ir)
                replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                    replayed_eids,
                    context.oracle_eids,
                )
                replay_only = replay_compare_eids - oracle_compare_eids
                oracle_only = oracle_compare_eids - replay_compare_eids
            residual_roots = _collect_residual_roots(
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            source_counts = inventory["source_counts"]
            compare_counts = inventory["compare_counts"]
            candidate_count = int(inventory["candidate_count"])
            candidate_ops = int(inventory["candidate_ops"])
            residual_inventory = _residual_candidate_inventory(
                inventory["candidate_summaries"],
                residual_roots=residual_roots,
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            residual_candidate_count = int(residual_inventory["residual_candidate_count"])
            residual_candidate_ops = int(residual_inventory["residual_candidate_ops"])
            residual_root_hits = set(residual_inventory["residual_root_hits"])
            defeated_residual_roots = residual_roots - residual_root_hits

            score = _primary_frontier_score(r, score_mode=score_mode)
            comparison_class = _effective_comparison_class(r)
            status = _frontier_status(
                candidate_count=candidate_count,
                residual_candidate_count=residual_candidate_count,
                residual_root_count=len(residual_roots),
                defeated_residual_root_count=len(defeated_residual_roots),
            )
            if residual_only and residual_candidate_count <= 0:
                continue
            print(
                f"{r.statute_id:<30} frontier={score:.1%} "
                f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                f"effects={r.n_effects:4d} candidate_effects={candidate_count:3d} "
                f"candidate_ops={candidate_ops:4d} "
                f"residual_candidate_effects={residual_candidate_count:3d} "
                f"residual_candidate_ops={residual_candidate_ops:4d}"
            )
            print(f"  class:    {comparison_class}")
            if source_counts:
                top_source = ", ".join(
                    f"{k}={v}" for k, v in source_counts.most_common(3)
                )
                print(f"  source:   {top_source}")
            if compare_counts:
                top_compare = ", ".join(
                    f"{k}={v}" for k, v in compare_counts.most_common(3)
                )
                print(f"  compare:  {top_compare}")
            if residual_roots:
                print(
                    f"  claims:   residual_roots={len(residual_roots):3d} "
                    f"backed_roots={len(residual_root_hits):3d} "
                    f"defeated_roots={len(defeated_residual_roots):3d}"
                )
            if not source_counts and not compare_counts:
                print("  source:   (none)")
                print("  compare:  (none)")
            print(f"  status:   {status}")
            print()
