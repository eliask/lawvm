from __future__ import annotations

from datetime import date
from typing import Any

from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import get_consolidated_oracle_inspection


def _selector_from_args(args: Any) -> ConsolidatedArtifactSelector:
    selector_mode = str(getattr(args, "selector_mode", "latest_cached_editorial") or "")
    if selector_mode == "latest_cached_editorial":
        return ConsolidatedArtifactSelector.latest_cached_editorial()
    if selector_mode == "bench_comparable":
        return ConsolidatedArtifactSelector.bench_comparable()
    if selector_mode == "exact_embedded_version":
        version_tag = str(getattr(args, "version_tag", "") or "").strip()
        if not version_tag:
            raise SystemExit("--version-tag is required for exact_embedded_version")
        return ConsolidatedArtifactSelector.exact_embedded_version(version_tag)
    if selector_mode == "date_consolidated_at_or_before":
        cutoff = str(getattr(args, "cutoff", "") or "").strip()
        if not cutoff:
            raise SystemExit("--cutoff is required for date_consolidated_at_or_before")
        try:
            cutoff_date = date.fromisoformat(cutoff)
        except ValueError as exc:
            raise SystemExit(f"invalid --cutoff date: {cutoff}") from exc
        return ConsolidatedArtifactSelector.date_consolidated_at_or_before(cutoff_date)
    raise SystemExit(f"unknown selector mode: {selector_mode}")


def _format_text(bundle: dict[str, Any]) -> str:
    cutoff_date = bundle.get("cutoff_date")
    cutoff_text = cutoff_date.isoformat() if cutoff_date else "(none)"
    locator = bundle.get("locator") or "(none)"
    oracle_version_amendment_id = bundle.get("oracle_version_amendment_id") or "(none)"
    selector_mode = bundle.get("selector_mode") or "(none)"
    return "\n".join(
        [
            f"Selector mode           : {selector_mode}",
            f"Selected oracle locator : {locator}",
            f"Oracle version amendment: {oracle_version_amendment_id}",
            f"Cutoff/consolidated date: {cutoff_text}",
        ]
    )


def main(args) -> None:
    selector = _selector_from_args(args)
    bundle = get_consolidated_oracle_inspection(args.statute_id, selector=selector)
    if getattr(args, "json", False):
        import json

        payload = vars(bundle).copy()
        cutoff_date = payload.get("cutoff_date")
        payload["cutoff_date"] = cutoff_date.isoformat() if cutoff_date else None
        print(json.dumps(
            {
                "statute_id": args.statute_id,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return
    print(_format_text(vars(bundle)))
