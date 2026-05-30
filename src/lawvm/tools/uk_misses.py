"""lawvm uk-misses -- full bucketed replay-vs-oracle EID miss worklist.

Prints every EID the oracle has that replay is MISSING ("only in oracle") and
every EID replay has that the oracle LACKS ("only in replayed"), bucketed by
structural container so the largest miss clusters surface first.  Includes the
compile-rejection tally so the human reader immediately sees which rule_ids
explain the gaps.

Usage:
    lawvm uk-misses ukpga/1998/42
    lawvm uk-misses ukpga/1998/42 --json
    lawvm uk-misses ukpga/1998/42 --db /path/to/uk_legislation.farchive
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


def _bucket_eid(eid: str) -> str:
    """Group an EID into its structural container bucket.

    Rule: strip all trailing leaf segments, keeping only the prefix up to and
    including the *first* numeric-or-numeric+alpha label component.  This
    places the entire ``section-23A`` family (``section-23A``,
    ``section-23A-2-a``, ``section-23A-3``) into bucket ``section-23A``, and
    ``schedule-1-paragraph-wrapper3-a`` into ``schedule-1-paragraph-wrapper3``.

    More precisely:

    1. Split the EID on ``-``.
    2. Walk parts left-to-right.  The first part that is purely alphabetic (no
       digits) is a *type component* (``section``, ``schedule``, ``part``,
       ``crossheading``, …).
    3. The *next* part (if present) is the *label component*, which may be
       alphanumeric (``23A``, ``1``, ``wrapper3``).  Together, the type +
       label form the bucket key.
    4. If no label component follows (e.g. the EID is bare like ``section``),
       return the whole EID as its own bucket.

    Examples::

        _bucket_eid("section-23A-2-a")          -> "section-23A"
        _bucket_eid("section-23A")               -> "section-23A"
        _bucket_eid("section-23A-3")             -> "section-23A"
        _bucket_eid("schedule-1-paragraph-wrapper3-a") -> "schedule-1"
        _bucket_eid("schedule-1-crossheading-abc-1")   -> "schedule-1"
        _bucket_eid("part-2-section-3-subsec-1") -> "part-2"
    """
    parts = [p for p in str(eid or "").split("-") if p]
    if not parts:
        return eid
    # Find the first purely-alphabetic type component
    type_idx: int | None = None
    for i, part in enumerate(parts):
        if part.isalpha():
            type_idx = i
            break
    if type_idx is None:
        # No alphabetic component found — return full EID
        return eid
    label_idx = type_idx + 1
    if label_idx >= len(parts):
        # No label follows the type — bucket is just the type
        return parts[type_idx]
    return "-".join(parts[: label_idx + 1])


def _bucket_eids(eids: set[str]) -> dict[str, list[str]]:
    """Group *eids* by bucket, sorted by descending cluster size."""
    buckets: dict[str, list[str]] = {}
    for eid in eids:
        bucket = _bucket_eid(eid)
        buckets.setdefault(bucket, []).append(eid)
    for members in buckets.values():
        members.sort()
    return dict(
        sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )


def _rejection_rule_histogram(
    lowering_rejections: list[dict[str, Any]],
) -> list[tuple[str, int, list[tuple[str, str]]]]:
    """Return (rule_id, count, [(affected_provisions, effect_type), ...]) tuples.

    Sorted by descending count.  Deduplicated (affected_provisions,
    effect_type) pairs per rule_id.
    """
    counter: Counter[str] = Counter()
    details: dict[str, set[tuple[str, str]]] = {}
    for row in lowering_rejections:
        rule_id = str(row.get("rule_id") or "unknown")
        counter[rule_id] += 1
        ap = str(row.get("affected_provisions") or "")
        et = str(row.get("effect_type") or "")
        details.setdefault(rule_id, set()).add((ap, et))
    result = []
    for rule_id, count in counter.most_common():
        pairs = sorted(details.get(rule_id, set()))
        result.append((rule_id, count, pairs))
    return result


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute, _get_all_eids
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.tools.uk_replay_regime import normalize_uk_replay_regime

    statute_id: str = args.statute_id
    json_output: bool = bool(getattr(args, "json", False))
    db_arg: str | None = getattr(args, "db", None)

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    replay_regime = normalize_uk_replay_regime(args)
    allow_oracle_alignment = replay_regime.allow_oracle_alignment
    allow_metadata_backfill = replay_regime.allow_metadata_backfill
    applicability_mode = replay_regime.applicability_mode
    authority_mode = replay_regime.authority_mode
    allow_metadata_only_effects = replay_regime.allow_metadata_only_effects

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(db_path) as archive:
        # 1. Load enacted base
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            print(
                f"error: enacted XML missing from archive for {enacted_url}",
                file=sys.stderr,
            )
            sys.exit(1)
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        # 2. Load oracle + extract EID map
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            print(
                f"error: oracle XML missing from archive for {oracle_url}",
                file=sys.stderr,
            )
            sys.exit(1)
        oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map: dict[str, str] = oracle_data.get("eid_map", {})
        text_map: dict[str, str] = oracle_data.get("text_map", {})
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )
        current_eids: set[str] = set(eid_map.values())

        # 3. Compile ops
        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=allow_metadata_backfill,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
            allow_metadata_only_effects=allow_metadata_only_effects,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        # 4. Apply ops
        replayed_ir = pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=allow_oracle_alignment,
        )

    # 5. Collect replayed EIDs
    replayed_eids: set[str] = _get_all_eids([replayed_ir.body], pit_date=None)
    for schedule in replayed_ir.supplements:
        replayed_eids.update(_get_all_eids([schedule], pit_date=None))

    # 6. Normalize + compare
    replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
        replayed_eids,
        current_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )
    common = replay_compare_eids & oracle_compare_eids
    only_in_oracle: set[str] = oracle_compare_eids - replay_compare_eids
    only_in_replayed: set[str] = replay_compare_eids - oracle_compare_eids
    similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)

    # 7. Bucket
    oracle_buckets = _bucket_eids(only_in_oracle)
    replayed_buckets = _bucket_eids(only_in_replayed)

    # 8. Rejection histogram (lowering rejections are the blocking compile misses)
    all_compile_rejections = [
        *effect_feed_parse_rejections,
        *lowering_rejections,
        *authority_rejections,
    ]
    rejection_histogram = _rejection_rule_histogram(all_compile_rejections)

    if json_output:
        print(
            json.dumps(
                {
                    "report_kind": "uk_misses_report",
                    "statute_id": statute_id,
                    "archive_path": str(db_path),
                    "similarity": similarity,
                    "replay_compare_eid_count": len(replay_compare_eids),
                    "oracle_compare_eid_count": len(oracle_compare_eids),
                    "common_eid_count": len(common),
                    "only_in_oracle_count": len(only_in_oracle),
                    "only_in_replayed_count": len(only_in_replayed),
                    "only_in_oracle_buckets": {
                        bucket: members for bucket, members in oracle_buckets.items()
                    },
                    "only_in_replayed_buckets": {
                        bucket: members for bucket, members in replayed_buckets.items()
                    },
                    "rejection_rule_counts": {
                        rule_id: count
                        for rule_id, count, _ in rejection_histogram
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    # Human-readable output
    print(f"Statute:  {statute_id}")
    print(f"Archive:  {db_path}")
    print(
        f"Similarity: {similarity:.4f} ({similarity:.1%})  "
        f"replay={len(replay_compare_eids)}  oracle={len(oracle_compare_eids)}  "
        f"common={len(common)}  "
        f"only_in_oracle={len(only_in_oracle)}  only_in_replayed={len(only_in_replayed)}"
    )
    print()

    print(f"ONLY IN ORACLE (replay MISSING) — {len(only_in_oracle)}")
    if oracle_buckets:
        for bucket, members in oracle_buckets.items():
            print(f"  {bucket}  ({len(members)})")
            for eid in members:
                print(f"    {eid}")
    else:
        print("  (none)")
    print()

    print(f"ONLY IN REPLAYED (replay EXTRA) — {len(only_in_replayed)}")
    if replayed_buckets:
        for bucket, members in replayed_buckets.items():
            print(f"  {bucket}  ({len(members)})")
            for eid in members:
                print(f"    {eid}")
    else:
        print("  (none)")
    print()

    print("COMPILE REJECTIONS")
    if rejection_histogram:
        for rule_id, count, pairs in rejection_histogram:
            print(f"  {count:4d}  {rule_id}")
            seen: set[tuple[str, str]] = set()
            for ap, et in pairs:
                if (ap, et) not in seen:
                    seen.add((ap, et))
                    ap_label = ap or "(none)"
                    et_label = et or "(none)"
                    print(f"         affected_provisions={ap_label}  effect_type={et_label}")
    else:
        print("  (none)")
