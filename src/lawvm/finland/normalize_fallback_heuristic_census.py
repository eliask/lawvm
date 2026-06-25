"""Whole-corpus load-bearing census for the rank-3 normalize fallback heuristics.

Three regex op-heuristics in :mod:`lawvm.finland.normalize` recover amendment
ops from the johtolause / amendment title when the typed grammar + LO
normalization chain produce NO ops:

  * :func:`~lawvm.finland.normalize.parse_ops_fallback_heuristic` — whole
    section / momentti repeal/replace/insert ops parsed from the johtolause text;
  * :func:`~lawvm.finland.normalize.parse_ops_fallback_heuristic_with_coverage`
    — the production shadow that wraps the bare heuristic and additionally
    surfaces passive regex span-coverage diagnostics (it delegates op production
    to the bare heuristic, so the two share one firing population);
  * :func:`~lawvm.finland.normalize.parse_ops_title_fallback` — title-only
    chapter/part/section repeal ops, recovered when the body yields no ops.

All three are gated behind ``allows_target_guessing`` in
:func:`lawvm.finland.frontend_compile.normalize_and_compile_ops` and fire ONLY
on the ``if not ops:`` (and one additive-subsection) branch — i.e. exactly when
the deterministic typed parse declined to produce ops. A full-corpus census
(every statute, including the ~tens-of-thousands of zero-amendment enactments)
measured at base ``f5843e95`` shows:

  * the johtolause heuristic CALL fires (returns >= 1 op) on ~5.3k statutes, but
    its ops reach FINAL compiled output (the load-bearing witness) on only a
    small set — the rest are discarded because the typed parse already produced
    ops (the additive/`if not ops` gate);
  * the title heuristic CALL fires on ~20 statutes, load-bearing on ~13.

This module is the COMMITTED, REGENERABLE proof that each heuristic is still
load-bearing: it runs the REAL production replay (``replay_xml`` via the same
entrypoint ``replay-all`` uses), captures ``compiled_ops_out``, and counts the
final compiled ops carrying each heuristic's extraction-provenance tag. A final
compiled op carrying the tag is, by construction, an op the heuristic produced
that the typed grammar did not — i.e. genuinely load-bearing output. The guard
test (:mod:`tests.test_fi_normalize_fallback_heuristic_census`) pins the
load-bearing op counts so the heuristics cannot silently grow OR silently fall to
zero (a fall to zero means the grammar absorbed them and they are now deletable).

This is a pure measurement addition: it imports the corpus + replay lazily and
changes no replay behaviour.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Literal, cast

# ---------------------------------------------------------------------------
# The extraction-provenance tag each heuristic stamps onto a load-bearing op.
# (frontend_compile sets these at the two heuristic call sites; they are the
# load-bearing witness — a final compiled op carrying the tag IS an op the
# heuristic produced and the typed grammar declined.)
# ---------------------------------------------------------------------------
JOHTO_FALLBACK_TAG = "extraction_fallback_heuristic"
TITLE_FALLBACK_TAG = "extraction_title_fallback"

#: report order for the heuristic lanes.
HEURISTIC_TAGS: tuple[str, ...] = (JOHTO_FALLBACK_TAG, TITLE_FALLBACK_TAG)


@dataclass(frozen=True)
class HeuristicCensusResult:
    """Outcome of a whole-corpus load-bearing census of the fallback heuristics."""

    #: statutes the production replay was attempted over (the denominator).
    total_scanned: int
    #: tag -> number of statutes with >= 1 final compiled op carrying the tag.
    load_bearing_statutes: dict[str, int]
    #: tag -> total number of final compiled ops carrying the tag.
    load_bearing_ops: dict[str, int]
    #: tag -> sorted sample of statute ids carrying the tag (spot-audit witness).
    sample_sids: dict[str, list[str]] = field(default_factory=dict)


def _scan_one(sid: str) -> tuple[str, dict[str, int]] | None:
    """Replay one statute; return (sid, {tag: n_compiled_ops_with_tag})."""
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import (
        ReplayXmlRequest,
        ReplayXmlSinks,
        call_replay_xml,
    )

    compiled_ops: list[dict[str, object]] = []
    try:
        call_replay_xml(
            replay_xml,
            request=ReplayXmlRequest(
                parent_id=sid,
                mode=cast(
                    Literal["official_consolidation", "legal_pit"],
                    "official_consolidation",
                ),
                quiet=True,
                build_full_products=True,
            ),
            sinks=ReplayXmlSinks(compiled_ops_out=compiled_ops),
        )
    except (NameError, TypeError, AttributeError):
        # Programming errors are not per-statute data faults — surface them.
        raise
    except Exception:  # lawvm-failloud: per-statute replay data fault is counted+skipped (no compiled ops to census); replay-all tracks crashes separately
        # A data fault still produced whatever compiled ops preceded it; count
        # those (the sink is populated in-place as ops compile).
        pass
    counts: dict[str, int] = {tag: 0 for tag in HEURISTIC_TAGS}
    for row in compiled_ops:
        raw_tags = row.get("extraction_provenance_tags")
        tags: list[object] = (
            list(raw_tags) if isinstance(raw_tags, (list, tuple)) else []
        )
        for tag in HEURISTIC_TAGS:
            if tag in tags:
                counts[tag] += 1
    if not any(counts.values()):
        return None
    return sid, counts


def _enumerate_statute_ids() -> list[str]:
    from lawvm.tools.replay_all import _enumerate_statute_ids as _enum

    return _enum()


def census_heuristic_load_bearing(
    limit: int = 0,
    *,
    workers: int = 16,
    sample_cap: int = 25,
) -> HeuristicCensusResult:
    """Census the load-bearing output of the three normalize fallback heuristics.

    Runs the production replay over every statute (or the first ``limit`` ids)
    and counts the FINAL compiled ops carrying each heuristic's
    extraction-provenance tag. Requires the canonical Finlex corpus
    (``LAWVM_CANONICAL_DATA_ROOT`` / a populated ``data/finlex.farchive``).
    """
    ids = _enumerate_statute_ids()
    if limit:
        ids = ids[:limit]

    lb_statutes: Counter[str] = Counter()
    lb_ops: Counter[str] = Counter()
    samples: dict[str, list[str]] = {tag: [] for tag in HEURISTIC_TAGS}

    def _consume(res: tuple[str, dict[str, int]] | None) -> None:
        if res is None:
            return
        sid, counts = res
        for tag, n in counts.items():
            if n <= 0:
                continue
            lb_statutes[tag] += 1
            lb_ops[tag] += n
            if len(samples[tag]) < sample_cap:
                samples[tag].append(sid)

    if workers and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(_scan_one, ids, chunksize=64):
                _consume(res)
    else:
        for sid in ids:
            _consume(_scan_one(sid))

    return HeuristicCensusResult(
        total_scanned=len(ids),
        load_bearing_statutes={tag: lb_statutes.get(tag, 0) for tag in HEURISTIC_TAGS},
        load_bearing_ops={tag: lb_ops.get(tag, 0) for tag in HEURISTIC_TAGS},
        sample_sids={tag: sorted(samples[tag]) for tag in HEURISTIC_TAGS},
    )


def result_to_json(result: HeuristicCensusResult) -> dict:
    """Render the census result as a deterministic, machine-readable dict."""
    return {
        "schema": "fi_normalize_fallback_heuristic_census.v1",
        "total_scanned": result.total_scanned,
        "load_bearing_statutes": {
            tag: result.load_bearing_statutes[tag] for tag in HEURISTIC_TAGS
        },
        "load_bearing_ops": {
            tag: result.load_bearing_ops[tag] for tag in HEURISTIC_TAGS
        },
        "sample_sids": {tag: result.sample_sids.get(tag, []) for tag in HEURISTIC_TAGS},
    }


def format_report(result: HeuristicCensusResult) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FI normalize rank-3 fallback-heuristic LOAD-BEARING census (whole corpus)")
    lines.append("=" * 72)
    lines.append(f"  statutes scanned : {result.total_scanned}")
    lines.append("-" * 72)
    for tag in HEURISTIC_TAGS:
        lines.append(
            f"  {tag:<32}: {result.load_bearing_statutes[tag]:6d} statutes  "
            f"{result.load_bearing_ops[tag]:6d} ops"
        )
        sids = result.sample_sids.get(tag, [])
        if sids:
            lines.append(f"      sample sids: {', '.join(sids[:10])}")
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> None:
    import sys

    args = sys.argv[1:]
    emit_json = "--json" in args
    positional = [a for a in args if not a.startswith("-")]
    limit = int(positional[0]) if positional else 0
    result = census_heuristic_load_bearing(limit=limit)
    if emit_json:
        print(json.dumps(result_to_json(result), indent=2, ensure_ascii=False))
    else:
        print(format_report(result))


if __name__ == "__main__":
    main()
