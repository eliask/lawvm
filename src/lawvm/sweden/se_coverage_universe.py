"""Sweden (SE) coverage-scan universe — content-addressed corpus root.

Brings the SE aggregate coverage scan into the "no hidden universe" invariant
family (pro-note §6 — UniverseSpec as first-class object). Today
:func:`aggregate_se_official_coverage` emits bucket counts as a dict
(`genuine_match_count`, `oracle_version_mismatch_count`, ...) describing
"we scanned N acts and saw these buckets." That prose-style aggregate is a
SUMMARY, not a declared universe — a missing or surplus scanned act does not
change the bucket dict in a way a checker can detect.

This module commits the scanned corpus to a content-addressed ``set_root`` so:
* adding or dropping an SFS id from the scanned set changes the root;
* changing any one act's ``outcome`` / ``three-bucket`` / ``classification_counts``
  changes the root;
* the empty-scan case is a committed empty root (the v0 "declares nothing" case).

Honest scope — what this is and is NOT.

* It IS an evidence-plane dossier root committed over a projection (the
  coverage scan's per-act rows). The projection dict stays; this module
  adds the universe root over it.
* It does NOT verify the SET is *correct* (e.g. it does not check whether the
  scanned set equals the full archived amending-act set — that needs an
  enumeration source). It only makes a missing/surplus act re-detectable on
  subsequent runs.
* It does NOT enter any semantic-object hash (the evidence-plane invariant of
  :mod:`lawvm.core.assumption_register` — the root is a detached commit).

Maps pro-note §6 UniverseSpec; the projection-plane shape it commits over is
``aggregate_se_official_coverage``'s return dict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from lawvm.substrate.canonical_json import JsonValue
from lawvm.substrate.roots import leaf_hash, set_root

# Schema + domain for the SE coverage-scan universe. ``set_root`` saturates the
# domain over the leaf hashes; the universe_root is the committed SetRoot over
# all scanned act entry hashes.
_SCHEMA_SE_COVERAGE_UNIVERSE = "lawvm.se_coverage_universe.v0"
_DOMAIN_SE_COVERAGE_UNIVERSE = "se_coverage_scan_universe"

# The closed SET of valid ``outcome`` values the universe can record per act. A
# scan-act with an outcome outside this set raises KeyError when committed — so
# a new outcome class landing in the scan engine must register here.
_SE_COVERAGE_VALID_OUTCOMES = frozenset(
    {"replay_ok", "older_base_required", "error"}
)


def se_coverage_universe_entry(
    amending_sfs_id: str,
    *,
    base_sfs_id: str = "",
    outcome: str = "",
    bucket_genuine_match_count: int = 0,
    bucket_oracle_version_mismatch_count: int = 0,
    bucket_genuine_mismatch_count: int = 0,
    bucket_unknown_count: int = 0,
    recovery_mode: str = "",
) -> dict[str, JsonValue]:
    """Build one canonical per-act entry listable into a universe commit.

    Raises ``KeyError`` when ``outcome`` is non-empty AND not in the closed set —
    a new outcome class cannot silently land in the scan without also being
    registered here as a valid universe outcome (§1.10 fail-loud).

    The entry's set of fields is intentionally CLOSED (per-act attributes that
    contribute to identity): two scans with materially different fields MUST
    produce distinct leaf hashes — this is the load-bearing invariant for the
    missing/surplus act detector.
    """
    if outcome and outcome not in _SE_COVERAGE_VALID_OUTCOMES:
        raise KeyError(
            f"SE coverage-scan outcome {outcome!r} is not in the closed valid set "
            f"{sorted(_SE_COVERAGE_VALID_OUTCOMES)}. Either add the new outcome class "
            f"to scan_se_official_replay's outcome taxonomy and register it here in "
            f"_SE_COVERAGE_VALID_OUTCOMES, or fix the row's outcome string."
        )
    return {
        "schema": _SCHEMA_SE_COVERAGE_UNIVERSE,
        "amending_sfs_id": str(amending_sfs_id or ""),
        "base_sfs_id": str(base_sfs_id or ""),
        "outcome": str(outcome or ""),
        "recovery_mode": str(recovery_mode or ""),
        "bucket_genuine_match_count": int(bucket_genuine_match_count),
        "bucket_oracle_version_mismatch_count": int(bucket_oracle_version_mismatch_count),
        "bucket_genuine_mismatch_count": int(bucket_genuine_mismatch_count),
        "bucket_unknown_count": int(bucket_unknown_count),
    }


def se_coverage_universe_root(
    per_act_entries: Sequence[Mapping[str, JsonValue]],
) -> str:
    """Content-addressed SetRoot over per-act entries — the committed universe.

    Sort-stable by ``amending_sfs_id`` so the root is order-independent (a
    universe is a set, not a list). Empty input is a valid empty SetRoot (the
    v0 "declares nothing" case — the omission is committed to).

    A missing/surplus act between two runs changes the root; a per-act field
    change (e.g. outcome flipped) also changes the root because the entry
    identity is determined by its content-addressed leaf hash.
    """
    sorted_entries = sorted(
        per_act_entries, key=lambda e: str(e.get("amending_sfs_id") or "")
    )
    leaf_hashes = [
        leaf_hash(_DOMAIN_SE_COVERAGE_UNIVERSE, dict(entry)) for entry in sorted_entries
    ]
    return set_root(_DOMAIN_SE_COVERAGE_UNIVERSE, leaf_hashes)


__all__ = [
    "se_coverage_universe_entry",
    "se_coverage_universe_root",
]
