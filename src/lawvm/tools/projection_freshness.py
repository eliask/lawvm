"""Tier 2 projection freshness — staleness detection on READ.

The amendment-index cache fix (c6f266fa) closed ONE stale-artifact footgun: the
Finland amendment-discovery CSV silently went stale after a farchive refresh,
producing wrong replay PIT. The SAME staleness class affects every build-time
Tier 2 PROJECTION under ``data/{j}/{sv}/`` (``fi_he_corpus.parquet``,
``ops.parquet``, ``fi_preparatory_refs.parquet``, ...): each is regenerable from
a Tier 1 farchive, each records its source hash in a ``{name}.state.json``
sidecar (see :mod:`lawvm.tools.tier2_state`), but NOTHING checks that recorded
hash against the current farchive when a query command *reads* the projection.

``lawvm rebuild-indexes`` checks staleness on REBUILD. This module adds the
missing READ-side guard: when a query command resolves a projection file, it
compares the recorded ``source_farchive_hash`` to the current farchive
fingerprint and, on mismatch, emits a LOUD stderr warning pointing at the single
rebuild command. The check is deliberately cheap (size+mtime fingerprint, the
same proxy ``rebuild-indexes`` uses) so it never slows reads down.

The check is advisory by default (warn, don't block): a stale projection is
still better than no answer, and the warning makes the staleness visible instead
of silent. Set ``LAWVM_STRICT_FRESHNESS=1`` to turn the warning into a hard
error for CI / reproducibility contexts.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from lawvm.tools.tier2_state import read_state


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """Result of comparing a projection's recorded source hash to current.

    status:
        "fresh"     — recorded hash == current farchive hash.
        "stale"     — recorded hash != current farchive hash (projection behind).
        "no_state"  — projection exists but has no/invalid .state.json sidecar.
        "unknown"   — projection not in the registry, or no resolvable farchive
                      (cannot determine staleness; treated as advisory-quiet).
    """

    projection_name: str
    freshness_status: str
    recorded_hash: str
    current_hash: str

    @property
    def is_stale(self) -> bool:
        return self.freshness_status == "stale"


# ---------------------------------------------------------------------------
# Registry lookup (lazy to avoid import-time cost on every CLI command)
# ---------------------------------------------------------------------------


def _projection_spec(projection_name: str, jurisdiction: str):
    """Return the ProjectionSpec for a projection, or None if unregistered."""
    from lawvm.tools.rebuild_indexes import _projections_for

    for spec in _projections_for(jurisdiction):
        if spec.name == projection_name:
            return spec
    return None


def _data_root_for(data_dir: str, jurisdiction: str) -> str:
    """Return the farchive-containing data root for a Tier 2 projection dir.

    Projections live at ``data/{jurisdiction}/{schema_version}/`` but farchives
    live at the ``data/`` root. Walk up from the projection dir to the directory
    that holds the farchives. The canonical layout is ``data/fi/v1`` →
    ``data``; we strip a trailing ``{jurisdiction}/{schema_version}`` when
    present, else fall back to the conventional ``data`` root.
    """
    p = Path(data_dir)
    parts = p.parts
    # Canonical: .../<root>/<jurisdiction>/<schema_version>
    if len(parts) >= 2 and parts[-2] == jurisdiction:
        return str(Path(*parts[:-2])) if len(parts) > 2 else "."
    # Non-canonical override dir: assume farchives sit two levels up if that
    # directory exists, else the conventional "data" root.
    candidate = p.parent.parent
    if candidate != p and (candidate / "finlex.farchive").exists():
        return str(candidate)
    return "data"


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def check_projection_freshness(
    stem: str,
    data_dir: str,
    *,
    jurisdiction: str = "fi",
    schema_version: str = "v1",
) -> FreshnessVerdict:
    """Compare a projection's recorded source hash to the current farchive.

    Cheap: reads the small ``.state.json`` sidecar and stats the farchive
    (size+mtime), the same proxy ``rebuild-indexes`` uses. Never reads the
    parquet body.
    """
    spec = _projection_spec(stem, jurisdiction)
    if spec is None:
        return FreshnessVerdict(stem, "unknown", "", "")

    from lawvm.tools.tier2_state import primary_farchive_hash

    data_root = _data_root_for(data_dir, jurisdiction)
    current_hash = primary_farchive_hash(data_root, spec.tier_1_deps)
    if current_hash == "":
        # No resolvable farchive (e.g. test/CI without farchives). Cannot judge.
        return FreshnessVerdict(stem, "unknown", "", "")

    parquet_path = Path(data_dir) / f"{stem}.parquet"
    state = read_state(parquet_path)
    if state is None:
        return FreshnessVerdict(stem, "no_state", "", current_hash)

    recorded = state.source_farchive_hash
    if recorded == current_hash:
        return FreshnessVerdict(stem, "fresh", recorded, current_hash)
    return FreshnessVerdict(stem, "stale", recorded, current_hash)


# ---------------------------------------------------------------------------
# Source-age check (the farchive itself may be old → real law drifted)
# ---------------------------------------------------------------------------
#
# Distinct from staleness: a projection can be perfectly FRESH against its
# farchive while the FARCHIVE is weeks old. In that case the latest real-world
# legal state is simply unknowable from local data — recent statutes/amendments
# may exist that were never ingested. This is an advisory drift warning, keyed
# on the backing farchive's mtime, with a configurable threshold
# (LAWVM_SOURCE_AGE_WARN_DAYS, default 30; 0 disables).


def _source_age_threshold_days() -> int:
    raw = os.environ.get("LAWVM_SOURCE_AGE_WARN_DAYS")
    if raw is None:
        return 30
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def farchive_age_days(data_root: str, farchive_name: str) -> Optional[float]:
    """Return the backing farchive's age in days (by mtime), or None if absent."""
    import time

    path = Path(data_root) / farchive_name
    if not path.exists():
        return None
    age_sec = time.time() - path.stat().st_mtime
    return age_sec / 86400.0


# Track which farchives we've already age-warned about this process.
_AGE_WARNED: set[str] = set()


def warn_if_source_old(
    stem: str,
    data_dir: str,
    *,
    jurisdiction: str = "fi",
) -> None:
    """Advisory: warn (once per farchive per process) if the backing farchive is
    older than the configured threshold, so a user does not mistake "fresh
    projection" for "current law". Silent when LAWVM_SUPPRESS_FRESHNESS is set,
    the threshold is 0, or the farchive is absent/recent.
    """
    if _truthy(os.environ.get("LAWVM_SUPPRESS_FRESHNESS")):
        return
    threshold = _source_age_threshold_days()
    if threshold <= 0:
        return

    spec = _projection_spec(stem, jurisdiction)
    if spec is None:
        return
    data_root = _data_root_for(data_dir, jurisdiction)

    for dep in spec.tier_1_deps:
        if dep in _AGE_WARNED:
            continue
        age = farchive_age_days(data_root, dep)
        if age is None or age < threshold:
            continue
        _AGE_WARNED.add(dep)
        print(
            "\n"
            + "=" * 72
            + f"\n  SOURCE MAY BE OUT OF DATE: {dep} is {age:.0f} days old"
            + f" (threshold {threshold}d).\n"
            + "  The latest real-world legal state is unknowable from local data —\n"
            + "  recently published statutes/amendments may not be ingested yet.\n"
            + "  Refresh:  uv run lawvm sync-finlex-latest   (statutes)\n"
            + "            uv run lawvm sync-fi-proposals     (HE corpus)\n"
            + "  (set LAWVM_SOURCE_AGE_WARN_DAYS=0 to silence)\n"
            + "=" * 72
            + "\n",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Warning emission (deduplicated per-process)
# ---------------------------------------------------------------------------


# Track which (stem, data_dir) pairs we've already warned about this process so
# a command that reads several projections doesn't spam the same warning.
_WARNED: set[tuple[str, str]] = set()


def _rebuild_command(stem: str, jurisdiction: str) -> str:
    """Return the single command that rebuilds the stale projection(s)."""
    he_projections = {
        "fi_he_corpus",
        "fi_he_atoms",
        "fi_he_law_refs",
        "fi_he_signatures",
        "fi_he_branch_ops",
    }
    if stem in he_projections:
        return "uv run lawvm sync-fi-proposals --projection-only"
    return f"uv run lawvm rebuild-indexes -j {jurisdiction} --incremental"


def warn_if_stale(
    stem: str,
    data_dir: str,
    *,
    jurisdiction: str = "fi",
    schema_version: str = "v1",
) -> FreshnessVerdict:
    """Check freshness and emit a LOUD stderr warning if the projection is stale.

    Returns the verdict so callers can branch if they want. Deduplicates the
    warning per (stem, data_dir) per process. Honors ``LAWVM_STRICT_FRESHNESS``:
    when set to a truthy value, a stale (or missing-state) projection is a hard
    error (sys.exit) instead of a warning.

    Set ``LAWVM_SUPPRESS_FRESHNESS=1`` to silence entirely (used by rebuild
    tooling and tests that intentionally operate on stale artifacts).
    """
    if _truthy(os.environ.get("LAWVM_SUPPRESS_FRESHNESS")):
        return FreshnessVerdict(stem, "unknown", "", "")

    # Advisory source-age drift warning (independent of staleness): even a fresh
    # projection over a weeks-old farchive cannot know recently published law.
    try:
        warn_if_source_old(stem, data_dir, jurisdiction=jurisdiction)
    except Exception:
        pass

    try:
        verdict = check_projection_freshness(
            stem,
            data_dir,
            jurisdiction=jurisdiction,
            schema_version=schema_version,
        )
    except Exception:
        # Freshness is advisory; never let it break a real query.
        return FreshnessVerdict(stem, "unknown", "", "")

    if verdict.freshness_status not in ("stale", "no_state"):
        return verdict

    key = (stem, str(Path(data_dir).resolve()))
    if key in _WARNED:
        return verdict
    _WARNED.add(key)

    rebuild_cmd = _rebuild_command(stem, jurisdiction)
    strict = _truthy(os.environ.get("LAWVM_STRICT_FRESHNESS"))

    if verdict.freshness_status == "stale":
        lines = [
            "",
            "=" * 72,
            f"  STALE PROJECTION: {stem}.parquet is behind its source farchive.",
            f"    recorded source hash: {verdict.recorded_hash or '(none)'}",
            f"    current  source hash: {verdict.current_hash}",
            "  Query results may omit recent statutes/amendments/HEs.",
            f"  Rebuild with:  {rebuild_cmd}",
            "=" * 72,
            "",
        ]
    else:  # no_state
        lines = [
            "",
            "=" * 72,
            f"  PROJECTION HAS NO FRESHNESS STATE: {stem}.parquet has no valid",
            f"    {stem}.state.json sidecar — staleness cannot be verified.",
            f"  Rebuild to (re)write the sidecar:  {rebuild_cmd}",
            "=" * 72,
            "",
        ]

    msg = "\n".join(lines)
    print(msg, file=sys.stderr)

    if strict:
        print(
            f"error: LAWVM_STRICT_FRESHNESS set and {stem} is {verdict.freshness_status}.",
            file=sys.stderr,
        )
        sys.exit(2)

    return verdict


def _truthy(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() not in ("", "0", "false", "no")


# ---------------------------------------------------------------------------
# Sweep helper (used by the `sync` verb / freshness audits)
# ---------------------------------------------------------------------------


def sweep_freshness(
    data_dir: str = "data/fi/v1",
    *,
    jurisdiction: str = "fi",
    schema_version: str = "v1",
) -> Dict[str, FreshnessVerdict]:
    """Return {projection_name: verdict} for every registered projection.

    Quiet (no stderr) — for programmatic use by the `sync` verb and tests.
    """
    from lawvm.tools.rebuild_indexes import _projections_for

    out: Dict[str, FreshnessVerdict] = {}
    for spec in _projections_for(jurisdiction):
        out[spec.name] = check_projection_freshness(
            spec.name,
            data_dir,
            jurisdiction=jurisdiction,
            schema_version=schema_version,
        )
    return out
