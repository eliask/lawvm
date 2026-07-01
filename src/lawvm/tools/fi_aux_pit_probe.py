"""fi_aux_pit_probe.py — read-only prototype for the all-historical-PIT aux target (#131).

The MAIN FI bench compares the fully-replayed statute against a SINGLE oracle
snapshot (the latest self-comparable published consolidation, or one date
cutoff). Finlex, however, publishes MULTIPLE selected consolidation snapshots
over a statute's life at ``finlex://sd-cons/{y}/{n}/fin@{version_tag}/main.xml``.

This probe turns those N snapshots into N comparison points: for each published
snapshot it (1) derives the snapshot's as-of date from the embedded version tag's
own amendment effective date, (2) materializes the ``legal_pit`` replay at that
date, (3) fetches THAT snapshot's oracle via ``exact_embedded_version``, and
(4) scores structural similarity with the SAME section-diff / neutralization /
penalty machinery the main bench uses.

It is READ-ONLY and ADDITIVE:
- it never mutates the corpus or replay,
- it does not wire into the main bench or change the headline number,
- it hard-reuses the bench's structural scorer so per-snapshot numbers are
  commensurable with the headline metric.

CLI::

    LAWVM_CANONICAL_DATA_ROOT=... uv run python -m lawvm.tools.fi_aux_pit_probe 2015/359 2016/1385

The core scoring split into a pure helper (:func:`score_pit_vs_oracle_tree`) so
it is unit-testable without a corpus.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import lxml.etree as etree

from lawvm.core.xml_parse import parse_corpus_xml


# ---------------------------------------------------------------------------
# Snapshot enumeration + as-of derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotPlan:
    """One published consolidation snapshot placed on the statute timeline."""

    sid: str
    version_tag: str
    amendment_id: str
    as_of: Optional[dt.date]
    date_consolidated: Optional[dt.date]
    reason: str = ""  # non-empty ⇒ snapshot could not be placed (skipped)


def _amendment_as_of_date(archive: Any, amendment_id: str) -> Optional[dt.date]:
    """As-of date for a snapshot's embedded amendment.

    Mirrors ``consolidated_store._is_self_comparable_with_tolerance`` exactly:
    the ordering date is the amendment's effective date, falling back to the
    statute issue date. ``date_consolidated`` is deliberately NOT used — for
    multi-version statutes Finlex collapses it to a single batch date shared by
    every version (the 2013/331 "Option Z" pathology), so it cannot order the
    snapshots.
    """
    from lawvm.corpus_store import statute_url
    from lawvm.finland.metadata import (
        _amendment_effective_date,
        _statute_issue_date,
    )

    source_bytes = archive.get(statute_url(amendment_id))
    if source_bytes is None:
        return None
    try:
        tree = parse_corpus_xml(source_bytes)
    except etree.XMLSyntaxError:
        return None
    return _amendment_effective_date(tree) or _statute_issue_date(tree)


def plan_snapshots(archive: Any, sid: str, *, lang: str = "fin") -> list[SnapshotPlan]:
    """Enumerate published snapshots for *sid* and derive each one's as-of date.

    Returned in chronological as-of order (unplaceable snapshots last).
    """
    from lawvm.finland.consolidated_store import (
        _version_tag_to_amendment_id,
        list_cached_consolidated_artifacts,
    )

    plans: list[SnapshotPlan] = []
    for art in list_cached_consolidated_artifacts(archive, sid, lang=lang):
        amendment_id = _version_tag_to_amendment_id(art.version_tag)
        if not amendment_id:
            plans.append(
                SnapshotPlan(
                    sid=sid,
                    version_tag=art.version_tag,
                    amendment_id="",
                    as_of=None,
                    date_consolidated=art.date_consolidated,
                    reason="non-amendment version tag",
                )
            )
            continue
        as_of = _amendment_as_of_date(archive, amendment_id)
        plans.append(
            SnapshotPlan(
                sid=sid,
                version_tag=art.version_tag,
                amendment_id=amendment_id,
                as_of=as_of,
                date_consolidated=art.date_consolidated,
                reason="" if as_of is not None else "no derivable as-of date",
            )
        )
    plans.sort(key=lambda p: (p.as_of is None, p.as_of or dt.date.max, p.version_tag))
    return plans


# ---------------------------------------------------------------------------
# Pure per-snapshot structural scorer (corpus-free, unit-testable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotScore:
    sid: str
    version_tag: str
    amendment_id: str
    as_of: Optional[dt.date]
    struct_sim: float           # in [0,1]; -1.0 if oracle absent / not scored
    n_sections: int             # non-editorial section count (denominator)
    n_penalized: int
    status: str
    event_counts: Counter


def score_pit_vs_oracle_tree(
    replay_master: Any,
    oracle_root: Any,
) -> tuple[float, int, int, Counter]:
    """Structural similarity of a materialized PIT vs one oracle tree.

    Returns ``(struct_sim, n_non_editorial_sections, n_penalized, event_counts)``.
    ``struct_sim`` is ``-1.0`` when the oracle content is absent.

    This replicates the bench's section-diff + neutralization + penalty
    accounting (``compute_statute_section_diffs`` section loop, then
    ``_structural_sim``) but against an EXPLICIT oracle tree rather than the
    module-level ``_BENCH_CONSOLIDATED_SELECTOR``. Keeping it a pure function of
    ``(replay_master, oracle_root)`` makes per-snapshot numbers commensurable
    with the headline metric and lets tests exercise it with hand-crafted trees.
    """
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.finland.oracle_comparison import (
        is_segmentation_displacement_neutralized,
    )
    from lawvm.semantic.contracts import build_semantic_diff_support
    from lawvm.semantic.structure import (
        semantic_structure_from_ir,
        semantic_structure_from_oracle,
    )
    from lawvm.tools.bench import (
        _clean,
        _clean_oracle_section_text,
        _comparison_ir,
        _section_diff_is_bench_neutralized,
    )
    from lawvm.tools.section_keys import (
        extract_ir_sections,
        extract_oracle_section_alternates,
        extract_oracle_sections,
        oracle_amb_alternate_match,
        reconcile_unique_unscoped_aliases,
    )
    from lawvm.tools.structural_review import (
        _sections_with_diffs,
        is_oracle_content_absent,
    )

    if is_oracle_content_absent(oracle_root):
        return -1.0, 0, 0, Counter()

    replay_ir = _comparison_ir(replay_master)
    replay_sections = extract_ir_sections(replay_ir)
    oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}
    oracle_alternates = (
        extract_oracle_section_alternates(oracle_root) if oracle_root is not None else {}
    )
    replay_sections, oracle_sections = reconcile_unique_unscoped_aliases(
        replay_sections, oracle_sections
    )

    sections: dict[str, dict[str, Any]] = {}
    for key in sorted(set(replay_sections) | set(oracle_sections)):
        replay_node = replay_sections.get(key)
        oracle_node = oracle_sections.get(key)
        replay_sem = (
            semantic_structure_from_ir(replay_node) if replay_node is not None else None
        )
        oracle_sem = (
            semantic_structure_from_oracle(oracle_node) if oracle_node is not None else None
        )
        item = build_semantic_diff_support(replay_sem, oracle_sem)
        if not item:
            continue
        amb_witness = (
            oracle_amb_alternate_match(
                key,
                _clean(irnode_to_text(replay_node)),
                oracle_alternates.get(key),
                _clean_oracle_section_text,
            )
            if replay_node is not None
            else None
        )
        if amb_witness is not None:
            item["amb_alternate_match"] = True
        else:
            sd_payload = item.get("semantic_diff")
            if isinstance(sd_payload, dict):
                sd_events = sd_payload.get("events", [])
                if isinstance(sd_events, list) and is_segmentation_displacement_neutralized(
                    sd_payload, sd_events
                ):
                    item["seg_displacement_match"] = True
        sections[key] = item

    non_editorial = {
        k: v
        for k, v in sections.items()
        if v.get("semantic_diff", {}).get("kind") != "editorial_only"
    }
    if not non_editorial:
        return 1.0, 0, 0, Counter()

    event_counts: Counter = Counter()
    penalized = 0
    for sec_key, sd, events in _sections_with_diffs({"sections": non_editorial}):
        for event in events:
            event_counts[event.get("kind", "unknown")] += 1
        if _section_diff_is_bench_neutralized(sd, events):
            continue
        if non_editorial.get(sec_key, {}).get("amb_alternate_match"):
            continue
        if non_editorial.get(sec_key, {}).get("seg_displacement_match"):
            continue
        penalized += 1

    struct_sim = 1.0 - penalized / len(non_editorial)
    return struct_sim, len(non_editorial), penalized, event_counts


# ---------------------------------------------------------------------------
# Per-statute driver (materialize replay + fetch snapshot oracle + score)
# ---------------------------------------------------------------------------


def score_snapshot(sid: str, plan: SnapshotPlan, *, corpus: Any = None) -> SnapshotScore:
    """Materialize the legal_pit replay at *plan.as_of* and score vs its oracle."""
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lawvm.finland.corpus import get_corpus, get_ground_truth_tree
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml

    if plan.as_of is None:
        return SnapshotScore(
            sid=sid,
            version_tag=plan.version_tag,
            amendment_id=plan.amendment_id,
            as_of=None,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            status=f"UNPLACEABLE:{plan.reason}",
            event_counts=Counter(),
        )
    if corpus is None:
        corpus = get_corpus()

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=sid,
            mode="legal_pit",
            as_of=plan.as_of.isoformat(),
            quiet=True,
            corpus=corpus,
        ),
    )
    oracle_root = get_ground_truth_tree(
        sid,
        corpus=corpus,
        selector=ConsolidatedArtifactSelector.exact_embedded_version(plan.version_tag),
    )
    if oracle_root is None:
        return SnapshotScore(
            sid=sid,
            version_tag=plan.version_tag,
            amendment_id=plan.amendment_id,
            as_of=plan.as_of,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            status="NO_ORACLE_FOR_VERSION",
            event_counts=Counter(),
        )

    struct_sim, n_sections, n_penalized, events = score_pit_vs_oracle_tree(
        master, oracle_root
    )
    status = "OK" if struct_sim >= 0 else "ORACLE_CONTENT_ABSENT"
    return SnapshotScore(
        sid=sid,
        version_tag=plan.version_tag,
        amendment_id=plan.amendment_id,
        as_of=plan.as_of,
        struct_sim=struct_sim,
        n_sections=n_sections,
        n_penalized=n_penalized,
        status=status,
        event_counts=events,
    )


def probe_statute(sid: str, *, corpus: Any = None) -> list[SnapshotScore]:
    """Score every published snapshot of *sid* against its own-version oracle."""
    from lawvm.finland.corpus import _archive_from_source, get_corpus

    if corpus is None:
        corpus = get_corpus()
    archive = _archive_from_source(corpus)
    if archive is None:
        raise RuntimeError("corpus store exposes no archive backend")
    plans = plan_snapshots(archive, sid)
    return [score_snapshot(sid, p, corpus=corpus) for p in plans]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt_sim(sim: float) -> str:
    return "   n/a" if sim < 0 else f"{100 * sim:6.2f}%"


def _print_statute(sid: str, scores: Iterable[SnapshotScore]) -> None:
    scores = list(scores)
    print(f"\n=== {sid}  ({len(scores)} published snapshots) ===")
    print(f"  {'as_of':<12} {'version':<10} {'amend':<10} {'struct':>7} "
          f"{'secs':>5} {'pen':>4}  status")
    traj: list[str] = []
    for s in scores:
        as_of = s.as_of.isoformat() if s.as_of else "-"
        print(f"  {as_of:<12} {s.version_tag:<10} {s.amendment_id:<10} "
              f"{_fmt_sim(s.struct_sim):>7} {s.n_sections:>5} {s.n_penalized:>4}  {s.status}")
        traj.append("n/a" if s.struct_sim < 0 else f"{100 * s.struct_sim:.1f}")
    scored = [s.struct_sim for s in scores if s.struct_sim >= 0]
    if scored:
        latest_scored = next(
            (s.struct_sim for s in reversed(scores) if s.struct_sim >= 0), None
        )
        mn = min(scored)
        hidden = latest_scored is not None and mn < latest_scored - 1e-9
        print(f"  trajectory: {' -> '.join(traj)}")
        print(f"  min-over-life={100 * mn:.2f}%  latest={_fmt_sim(latest_scored or -1.0).strip()}"
              f"  hidden-mid-life-divergence={'YES' if hidden else 'no'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lawvm.tools.fi_aux_pit_probe",
        description="Read-only prototype: score replay@as_of vs each published "
        "consolidation snapshot's own oracle (#131 aux target).",
    )
    parser.add_argument("statute_ids", nargs="+", help="e.g. 2015/359 2016/1385")
    args = parser.parse_args(argv)

    for sid in args.statute_ids:
        try:
            scores = probe_statute(sid)
        except Exception as exc:  # noqa: BLE001 — prototype: surface, don't crash the batch
            print(f"\n=== {sid} === ERROR: {exc}", file=sys.stderr)
            continue
        _print_statute(sid, scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
