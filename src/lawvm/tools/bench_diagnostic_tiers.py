"""Tiered aggregation for Finland bench replay diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

BenchDiagnosticTier = Literal[
    "operative",
    "timeline_robust",
    "timeline_variant",
    "temporal",
    "structural",
    "audit",
    "oracle",
]

_TIER_ORDER: tuple[BenchDiagnosticTier, ...] = (
    "timeline_robust",
    "operative",
    "temporal",
    "structural",
    "oracle",
    "timeline_variant",
    "audit",
)

_REGISTRY_AUDIT_KINDS = frozenset(
    {
        "ELAB.REGISTRY_STAGE",
        "ELAB.REGISTRY_PIPELINE",
    }
)

_TEMPORAL_FINDING_PREFIXES = ("TIME.",)
_OPERATIVE_FINDING_PREFIXES = ("ELAB.", "APPLY.", "PARSE.", "REPLAY.")


def _finding_kind_from_key(key: str) -> str:
    if key.startswith("finding:"):
        return key.removeprefix("finding:")
    return ""


def classify_bench_diagnostic_key(key: str) -> BenchDiagnosticTier:
    """Map a raw bench diagnostic counter key to a display tier."""
    if key.startswith("timeline_robust:"):
        return "timeline_robust"
    if key.startswith("timeline_variant:"):
        return "timeline_variant"
    if key.startswith("structural:"):
        return "structural"
    if key in {"source_adjudication:oracle_suspect", "oracle_suspect"}:
        return "oracle"
    if key in {"timeline_invariant"}:
        return "timeline_robust"
    if key in {"same_day_empty_interval"}:
        return "temporal"

    finding_kind = _finding_kind_from_key(key)
    if finding_kind:
        if finding_kind == "timeline_invariant_violation":
            return "timeline_robust"
        if finding_kind in _REGISTRY_AUDIT_KINDS:
            return "audit"
        if finding_kind.startswith(_TEMPORAL_FINDING_PREFIXES):
            return "temporal"
        if finding_kind.startswith(_OPERATIVE_FINDING_PREFIXES):
            return "operative"
        return "audit"

    if key in {
        "coverage_degraded",
        "tree_invariant",
        "text_duplication",
        "source_pathology",
        "product_invariant",
        "warning_other",
    }:
        return "audit"
    return "audit"


def _collapse_audit_display_key(key: str) -> str:
    if key == "source_adjudication:oracle_suspect":
        return "oracle_suspect"
    finding_kind = _finding_kind_from_key(key)
    if finding_kind in _REGISTRY_AUDIT_KINDS:
        return "registry_stage"
    if key.startswith("finding:"):
        return finding_kind or key.removeprefix("finding:")
    if key.startswith("structural:"):
        return key.removeprefix("structural:")
    if key.startswith("timeline_robust:"):
        return key.removeprefix("timeline_robust:")
    if key.startswith("timeline_variant:"):
        return key.removeprefix("timeline_variant:")
    return key


def enrich_bench_finding_counts(master: Any) -> Counter[str]:
    """Split typed replay findings into tier-aware counter keys."""
    counts: Counter[str] = Counter()
    for finding in getattr(master, "findings", ()) or ():
        kind = str(getattr(finding, "kind", "") or "").strip()
        if not kind:
            continue
        detail = getattr(finding, "detail", None) or {}
        if kind == "timeline_invariant_violation":
            code = str(detail.get("code") or detail.get("kind") or "unknown")
            tier = str(detail.get("tier") or "robust")
            if tier == "materialization_variant":
                counts[f"timeline_variant:{code}"] += 1
            else:
                counts[f"timeline_robust:{code}"] += 1
            continue
        counts[f"finding:{kind}"] += 1
    source_adjudication = getattr(master, "source_adjudication", None)
    if source_adjudication is not None and getattr(source_adjudication, "oracle_suspect", ""):
        counts["source_adjudication:oracle_suspect"] += 1
    return counts


def merge_bench_diagnostic_counts(
    captured_counts: Counter[str],
    finding_counts: Counter[str],
) -> Counter[str]:
    """Merge stdout/stderr captures with typed finding counters."""
    merged = Counter(captured_counts)
    for key, count in finding_counts.items():
        if count:
            merged[key] += count
    return merged


def format_tiered_bench_warning_summary(diagnostics: Counter[str]) -> str:
    """Render diagnostics grouped by tier with audit registry collapse."""
    if not diagnostics:
        return ""

    tier_buckets: dict[BenchDiagnosticTier, Counter[str]] = {
        tier: Counter() for tier in _TIER_ORDER
    }
    for key, count in diagnostics.items():
        if count <= 0:
            continue
        tier = classify_bench_diagnostic_key(key)
        display_key = _collapse_audit_display_key(key)
        tier_buckets[tier][display_key] += count

    label = (
        "diagnostics"
        if any(
            key.startswith(("finding:", "source_adjudication:", "timeline_robust:", "timeline_variant:"))
            for key in diagnostics
        )
        else "warnings"
    )

    tier_parts: list[str] = []
    for tier in _TIER_ORDER:
        bucket = tier_buckets[tier]
        if not bucket:
            continue
        inner = ", ".join(
            f"{kind}×{count}"
            for kind, count in sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
        )
        tier_parts.append(f"{tier}: {inner}")

    return f"  {label}: " + " | ".join(tier_parts)


def merge_diagnostic_counter_dicts(
    counters: Sequence[Mapping[str, int]],
) -> Counter[str]:
    """Merge per-statute diagnostic counter maps into one rollup."""
    merged: Counter[str] = Counter()
    for counter in counters:
        for key, count in counter.items():
            if count:
                merged[key] += int(count)
    return merged


def bench_diagnostic_sidecar_rows(
    *,
    label: str,
    statute_id: str,
    amendment_count: int,
    counts: Mapping[str, int],
) -> list[dict[str, object]]:
    """Expand one statute's diagnostic counter into JSONL sidecar rows."""
    rows: list[dict[str, object]] = []
    for key, count in sorted(counts.items()):
        if count <= 0:
            continue
        rows.append(
            {
                "schema": "fi_bench_diagnostic.v1",
                "label": label,
                "statute_id": statute_id,
                "amendment_count": amendment_count,
                "diagnostic_tier": classify_bench_diagnostic_key(key),
                "diagnostic_key": key,
                "count": int(count),
            }
        )
    return rows


def print_bench_diagnostic_tier_rollup(merged: Counter[str]) -> None:
    """Print run-end tier rollup when diagnostic replay captured findings."""
    summary = format_tiered_bench_warning_summary(merged)
    if summary:
        print(f"\nDiagnostic tier rollup:{summary}")
