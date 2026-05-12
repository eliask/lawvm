"""Inventory helpers for Norway public Lovdata archives."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lawvm.norway.commencement import (
    apply_no_commencement_overrides,
    load_no_commencement_overrides,
)
from lawvm.norway.index import NOAmendmentIndex, build_no_amendment_index, load_no_amendment_index
from lawvm.norway.sources import (
    iter_no_current_artifacts,
    load_available_lti_law_ids,
    load_no_current_law_ids,
    load_no_current_law_titles,
    resolve_no_source_path,
)


@dataclass
class NOInventory:
    data_dir: Path
    current_law_ids: set[str] = field(default_factory=set)
    current_law_ids_with_local_base_source: set[str] = field(default_factory=set)
    amendment_status_counts: Counter[str] = field(default_factory=Counter)
    base_to_statuses: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    base_to_sources: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    malformed_base_refs: Counter[str] = field(default_factory=Counter)
    current_law_source_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def law_status_map(self) -> dict[str, str]:
        laws_with_amendments = self.current_law_ids & set(self.base_to_statuses)
        statuses = {base_id: self._base_replay_status(base_id) for base_id in laws_with_amendments}
        for base_id in self.current_law_ids - laws_with_amendments:
            statuses[base_id] = "no_amendments"
        return statuses

    def amended_executable_law_status_map(self) -> dict[str, str]:
        amended_current_laws = self.current_law_ids & set(self.base_to_statuses)
        executable_current_laws = amended_current_laws & self.current_law_ids_with_local_base_source
        return {base_id: self._base_replay_status(base_id) for base_id in executable_current_laws}

    def to_dict(self) -> dict[str, Any]:
        status_map = self.law_status_map()
        executable_status_map = self.amended_executable_law_status_map()
        laws_with_amendments = {base_id for base_id, status in status_map.items() if status != "no_amendments"}
        fully_replayable = {base_id for base_id, status in status_map.items() if status == "fully_replayable"}
        blocked_contingent = {base_id for base_id, status in status_map.items() if status == "blocked_contingent"}
        blocked_unknown = {base_id for base_id, status in status_map.items() if status == "blocked_unknown"}
        no_amendments = {base_id for base_id, status in status_map.items() if status == "no_amendments"}
        executable_fully_replayable = {
            base_id for base_id, status in executable_status_map.items() if status == "fully_replayable"
        }
        executable_blocked_contingent = {
            base_id for base_id, status in executable_status_map.items() if status == "blocked_contingent"
        }
        executable_blocked_unknown = {
            base_id for base_id, status in executable_status_map.items() if status == "blocked_unknown"
        }
        missing_base_source = laws_with_amendments - self.current_law_ids_with_local_base_source

        top_blocked = sorted(
            (
                (base_id, len(self.base_to_sources[base_id]))
                for base_id in blocked_contingent | blocked_unknown
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10]

        top_replayable = sorted(
            (
                (base_id, len(self.base_to_sources[base_id]))
                for base_id in fully_replayable
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10]

        top_executable_blocked = sorted(
            (
                (base_id, len(self.base_to_sources[base_id]))
                for base_id in executable_blocked_contingent | executable_blocked_unknown
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10]

        top_executable_replayable = sorted(
            (
                (base_id, len(self.base_to_sources[base_id]))
                for base_id in executable_fully_replayable
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10]

        top_missing_base = sorted(
            (
                (base_id, len(self.base_to_sources[base_id]))
                for base_id in missing_base_source
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10]

        return {
            "data_dir": str(self.data_dir),
            "current_laws": len(self.current_law_ids),
            "current_laws_with_local_base_source": len(self.current_law_ids_with_local_base_source),
            "current_laws_without_local_base_source": len(
                self.current_law_ids - self.current_law_ids_with_local_base_source
            ),
            "amendment_documents": sum(self.amendment_status_counts.values()),
            "amendment_documents_by_status": dict(self.amendment_status_counts),
            "current_laws_with_amendments": len(laws_with_amendments),
            "current_laws_without_amendments": len(no_amendments),
            "current_laws_fully_replayable": len(fully_replayable),
            "current_laws_blocked_contingent": len(blocked_contingent),
            "current_laws_blocked_unknown": len(blocked_unknown),
            "current_laws_with_amendments_missing_base_source": len(missing_base_source),
            "current_laws_with_amendments_fully_replayable_executable": len(executable_fully_replayable),
            "current_laws_with_amendments_blocked_contingent_executable": len(executable_blocked_contingent),
            "current_laws_with_amendments_blocked_unknown_executable": len(executable_blocked_unknown),
            "top_blocked_current_laws": [
                {"base_id": base_id, "amendments": count}
                for base_id, count in top_blocked
            ],
            "top_executable_blocked_current_laws": [
                {"base_id": base_id, "amendments": count}
                for base_id, count in top_executable_blocked
            ],
            "top_fully_replayable_current_laws": [
                {"base_id": base_id, "amendments": count}
                for base_id, count in top_replayable
            ],
            "top_executable_fully_replayable_current_laws": [
                {"base_id": base_id, "amendments": count}
                for base_id, count in top_executable_replayable
            ],
            "top_missing_base_source_current_laws": [
                {"base_id": base_id, "amendments": count}
                for base_id, count in top_missing_base
            ],
            "malformed_base_refs": dict(self.malformed_base_refs),
            "current_law_source_diagnostic_count": len(self.current_law_source_diagnostics),
            "current_law_source_diagnostic_rule_counts": dict(
                Counter(str(row.get("rule_id") or "") for row in self.current_law_source_diagnostics)
            ),
            "current_law_source_diagnostics": list(self.current_law_source_diagnostics),
        }

    def _base_replay_status(self, base_id: str) -> str:
        statuses = self.base_to_statuses.get(base_id, [])
        if not statuses:
            return "no_amendments"
        if any(status == "contingent" for status in statuses):
            return "blocked_contingent"
        if any(status not in {"dated", "immediate", "override"} for status in statuses):
            return "blocked_unknown"
        return "fully_replayable"


def build_no_inventory(
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
) -> NOInventory:
    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)
    if commencement_path is not None:
        overrides = load_no_commencement_overrides(commencement_path)
        index = apply_no_commencement_overrides(index, overrides)
    inventory = NOInventory(data_dir=data_dir)

    current_law_source_diagnostics: list[dict[str, Any]] = []
    inventory.current_law_ids = load_no_current_law_ids(
        data_dir,
        diagnostics_out=current_law_source_diagnostics,
    )
    if not inventory.current_law_ids:
        fallback_ids = {artifact.logical_id for artifact in iter_no_current_artifacts(data_dir)}
        if fallback_ids:
            current_law_source_diagnostics.append(
                {
                    "rule_id": "no_inventory_current_law_id_artifact_fallback_used",
                    "family": "source_pathology",
                    "phase": "acquisition",
                    "reason": (
                        "Norway inventory used current artifact locators as a fallback because the current-law ID "
                        "parser returned no retained IDs."
                    ),
                    "fallback_current_law_count": len(fallback_ids),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
        inventory.current_law_ids = fallback_ids
    inventory.current_law_source_diagnostics = current_law_source_diagnostics
    inventory.current_law_ids_with_local_base_source = (
        inventory.current_law_ids & load_available_lti_law_ids(data_dir)
    )

    for entry in index.entries:
        inventory.amendment_status_counts[entry.effective_status] += 1
        for base_id in entry.base_ids:
            if not base_id.startswith("no/lov/"):
                inventory.malformed_base_refs[base_id] += 1
                continue
            inventory.base_to_statuses[base_id].append(entry.effective_status)
            inventory.base_to_sources[base_id].append(entry.source_id)

    return inventory


def build_no_missing_base_report(
    inventory: NOInventory,
    *,
    current_law_titles: Optional[dict[str, str]] = None,
    base_id: str | None = None,
    min_amendments: int = 1,
) -> dict[str, Any]:
    current_law_title_diagnostics: list[dict[str, Any]] = []
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(
            inventory.data_dir,
            diagnostics_out=current_law_title_diagnostics,
        )

    missing_base_source = sorted(
        (
            base_id_candidate,
            len(inventory.base_to_sources[base_id_candidate]),
        )
        for base_id_candidate in (
            (inventory.current_law_ids & set(inventory.base_to_statuses))
            - inventory.current_law_ids_with_local_base_source
        )
        if (base_id is None or base_id_candidate == base_id)
        and len(inventory.base_to_sources[base_id_candidate]) >= min_amendments
    )
    missing_base_source.sort(key=lambda item: (-item[1], item[0]))

    laws = [
        {
            "base_id": law_id,
            "title": current_law_titles.get(law_id, ""),
            "amendments": amendment_count,
            "source_ids": sorted(inventory.base_to_sources[law_id]),
        }
        for law_id, amendment_count in missing_base_source
    ]

    return {
        "data_dir": str(inventory.data_dir),
        "current_laws": len(inventory.current_law_ids),
        "missing_base_source_law_count": len(laws),
        "base_id_filter": base_id or "",
        "min_amendments": min_amendments,
        "laws": laws,
        "current_law_title_diagnostic_count": len(current_law_title_diagnostics),
        "current_law_title_diagnostic_rule_counts": dict(
            Counter(str(row.get("rule_id") or "") for row in current_law_title_diagnostics)
        ),
        "current_law_title_diagnostics": current_law_title_diagnostics,
    }
