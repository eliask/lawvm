"""Norway commencement sidecar support for contingent amendment acts."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

from lawvm.norway.index import NOAmendmentIndex
from lawvm.norway.sources import (
    load_available_lti_law_ids,
    load_no_current_law_ids,
    load_no_current_law_titles,
    resolve_no_source_path,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_no_commencement_phrase(raw_text: str) -> str:
    normalized = " ".join(raw_text.replace("\xa0", " ").split()).strip().rstrip(".").lower()
    if not normalized:
        return ""
    if "kongen bestemmer" in normalized:
        return "kongen bestemmer"
    if "kongen fastset" in normalized or "kongen fastsetter" in normalized:
        return "kongen fastsetter"
    if "departementet bestemmer" in normalized:
        return "departementet bestemmer"
    if "fastsettes ved lov" in normalized:
        return "ikrafttredelse fastsettes ved lov"
    if "fra den tid" in normalized:
        return "fra den tid ..."
    return normalized


def _normalize_phrase_filter(phrase: str | None) -> str:
    if not phrase:
        return ""
    return normalize_no_commencement_phrase(phrase)


def _normalize_override_state_filter(state: str | None) -> str:
    if not state:
        return ""
    normalized = state.strip().lower()
    if normalized not in {"blank", "untracked", "resolved"}:
        return ""
    return normalized


def load_no_commencement_overrides(path: Path) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "overrides" in raw:
        raw = raw["overrides"]
    if not isinstance(raw, dict):
        raise ValueError("commencement override file must be a JSON object")

    overrides: dict[str, dict[str, str]] = {}
    for source_id, value in raw.items():
        if not isinstance(source_id, str):
            continue
        if isinstance(value, str):
            overrides[source_id] = {
                "effective_date": value,
                "note": "",
                "resolution_kind": "",
                "evidence_source_id": "",
                "evidence_excerpt": "",
            }
            continue
        if isinstance(value, dict):
            effective_date = value.get("effective_date")
            if not isinstance(effective_date, str):
                effective_date = value.get("date") if isinstance(value.get("date"), str) else ""
            payload: dict[str, str] = {
                "effective_date": effective_date,
                "note": value.get("note", "") if isinstance(value.get("note"), str) else "",
                "resolution_kind": (
                    value.get("resolution_kind", "") if isinstance(value.get("resolution_kind"), str) else ""
                ),
                "evidence_source_id": (
                    value.get("evidence_source_id", "")
                    if isinstance(value.get("evidence_source_id"), str)
                    else ""
                ),
                "evidence_excerpt": (
                    value.get("evidence_excerpt", "")
                    if isinstance(value.get("evidence_excerpt"), str)
                    else ""
                ),
            }
            if isinstance(value.get("evidence_locator"), str):
                payload["evidence_locator"] = value["evidence_locator"]
            if isinstance(value.get("evidence_date"), str):
                payload["evidence_date"] = value["evidence_date"]
            overrides[source_id] = payload
    return overrides


def no_override_state(
    source_id: str,
    overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, str | bool]:
    if not overrides or source_id not in overrides:
        return {
            "override_state": "untracked",
            "override_effective_date": "",
            "override_note": "",
            "override_has_note": False,
            "override_resolution_kind": "",
            "override_evidence_source_id": "",
            "override_evidence_excerpt": "",
            "override_has_evidence": False,
        }
    payload = overrides[source_id]
    effective_date = payload.get("effective_date", "").strip()
    note = payload.get("note", "").strip()
    resolution_kind = payload.get("resolution_kind", "").strip()
    evidence_source_id = payload.get("evidence_source_id", "").strip()
    evidence_excerpt = payload.get("evidence_excerpt", "").strip()
    return {
        "override_state": "resolved" if effective_date else "blank",
        "override_effective_date": effective_date,
        "override_note": note,
        "override_has_note": bool(note),
        "override_resolution_kind": resolution_kind,
        "override_evidence_source_id": evidence_source_id,
        "override_evidence_excerpt": evidence_excerpt,
        "override_has_evidence": bool(resolution_kind or evidence_source_id or evidence_excerpt),
    }


def apply_no_commencement_overrides(
    index: NOAmendmentIndex,
    overrides: dict[str, dict[str, str]],
) -> NOAmendmentIndex:
    if not overrides:
        return index

    updated_entries = []
    for entry in index.entries:
        override = overrides.get(entry.source_id)
        if override is None:
            updated_entries.append(entry)
            continue
        effective_date = override.get("effective_date", "")
        if not effective_date:
            updated_entries.append(entry)
            continue
        note = override.get("note", "").strip()
        resolution_kind = override.get("resolution_kind", "").strip()
        evidence_source_id = override.get("evidence_source_id", "").strip()
        updated_entries.append(
            dc_replace(
                entry,
                effective_status="override",
                effective_date=effective_date,
                raw_date_in_force=(
                    f"{entry.raw_date_in_force} | override:{effective_date}"
                    + (f" | note:{note}" if note else "")
                    + (f" | resolution:{resolution_kind}" if resolution_kind else "")
                    + (f" | evidence:{evidence_source_id}" if evidence_source_id else "")
                ),
            )
        )

    return NOAmendmentIndex(
        data_dir=index.data_dir,
        source_kind=index.source_kind,
        generated_at_utc=index.generated_at_utc,
        archive_names=list(index.archive_names),
        archive_metadata=dict(index.archive_metadata),
        entries=updated_entries,
    )


def _load_no_executable_current_law_ids(data_dir: Path) -> set[str]:
    return load_no_current_law_ids(data_dir) & load_available_lti_law_ids(data_dir)


def _base_replay_status_from_statuses(statuses: list[str]) -> str:
    if not statuses:
        return "no_amendments"
    if any(status == "contingent" for status in statuses):
        return "blocked_contingent"
    if any(status not in {"dated", "immediate", "override"} for status in statuses):
        return "blocked_unknown"
    return "fully_replayable"


def _build_unresolved_blockers_by_current_law(
    index: NOAmendmentIndex,
    *,
    current_law_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    blockers_by_current_law: dict[str, list[dict[str, Any]]] = {}
    for entry in index.entries:
        if entry.effective_status not in {"contingent", "missing", "unknown"}:
            continue
        blocker_item = {
            "source_id": entry.source_id,
            "title": entry.title,
            "effective_status": entry.effective_status,
            "raw_date_in_force": entry.raw_date_in_force,
            "n_ops": entry.n_ops,
        }
        for base_id in entry.base_ids:
            if base_id in current_law_ids:
                blockers_by_current_law.setdefault(base_id, []).append(blocker_item)
    return blockers_by_current_law


def build_no_commencement_report(
    index: NOAmendmentIndex,
    *,
    base_id: str | None = None,
    phrase: str | None = None,
    override_state: str | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    current_law_ids: set[str] | None = None,
    current_laws_only: bool = False,
    sort_mode: str = "source",
) -> dict[str, Any]:
    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    phrase_filter = _normalize_phrase_filter(phrase)
    override_state_filter = _normalize_override_state_filter(override_state)
    if current_law_ids is None and (current_laws_only or sort_mode in {"impact", "unlock"}):
        current_law_ids = load_no_current_law_ids(data_dir)
    if current_law_ids is not None and sort_mode == "unlock":
        current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)

    unresolved: list[dict[str, Any]] = [
        {
            "source_id": entry.source_id,
            "title": entry.title,
            "effective_status": entry.effective_status,
            "raw_date_in_force": entry.raw_date_in_force,
            "base_ids": list(entry.base_ids),
            "current_law_base_ids": (
                [base for base in entry.base_ids if current_law_ids is None or base in current_law_ids]
            ),
            "n_ops": entry.n_ops,
            "archive": entry.archive,
            "member_name": entry.member_name,
        }
        for entry in index.entries
        if entry.effective_status in {"contingent", "missing", "unknown"}
        and (not phrase_filter or normalize_no_commencement_phrase(entry.raw_date_in_force) == phrase_filter)
    ]
    if base_id is not None:
        unresolved = [item for item in unresolved if base_id in item["base_ids"]]
    if current_laws_only:
        unresolved = [item for item in unresolved if item["current_law_base_ids"]]

    blockers_by_current_law: dict[str, list[str]] = {}
    for item in unresolved:
        for affected_base_id in item["current_law_base_ids"]:
            blockers_by_current_law.setdefault(affected_base_id, []).append(item["source_id"])

    for item in unresolved:
        current_law_base_ids = list(item["current_law_base_ids"])
        sole_blocker_current_laws = sorted(
            base_id
            for base_id in current_law_base_ids
            if blockers_by_current_law.get(base_id) == [item["source_id"]]
        )
        item["current_law_count"] = len(current_law_base_ids)
        item["sole_blocker_current_laws"] = sole_blocker_current_laws
        item["sole_blocker_current_law_count"] = len(sole_blocker_current_laws)
        item.update(no_override_state(str(item["source_id"]), overrides))

    if override_state_filter:
        unresolved = [
            item for item in unresolved if str(item.get("override_state", "")) == override_state_filter
        ]

    if sort_mode == "unlock":
        unresolved.sort(
            key=lambda item: (
                -item["sole_blocker_current_law_count"],
                -item["current_law_count"],
                -item["n_ops"],
                item["source_id"],
            )
        )
    elif sort_mode == "impact":
        unresolved.sort(
            key=lambda item: (
                -item["current_law_count"],
                -item["n_ops"],
                item["source_id"],
            )
        )
    else:
        unresolved.sort(key=lambda item: (item["effective_status"], item["source_id"]))

    counts: dict[str, int] = {}
    override_counts: dict[str, int] = {}
    for item in unresolved:
        status = item["effective_status"]
        counts[status] = counts.get(status, 0) + 1
        override_state = str(item.get("override_state", "untracked"))
        override_counts[override_state] = override_counts.get(override_state, 0) + 1

    return {
        "data_dir": index.data_dir,
        "unresolved_count": len(unresolved),
        "unresolved_by_status": counts,
        "override_state_counts": override_counts,
        "base_id_filter": base_id or "",
        "phrase_filter": phrase_filter,
        "override_state_filter": override_state_filter,
        "current_laws_only": current_laws_only,
        "sort_mode": sort_mode,
        "entries": unresolved,
    }


def build_no_source_report(
    index: NOAmendmentIndex,
    *,
    source_id: str,
    overrides: dict[str, dict[str, str]] | None = None,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    from lawvm.norway.sources import iter_no_current_artifacts

    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir)
        if not current_law_ids:
            current_law_ids = {artifact.logical_id for artifact in iter_no_current_artifacts(data_dir)}
    if executable_current_law_ids is None:
        executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)

    entry = next((candidate for candidate in index.entries if candidate.source_id == source_id), None)
    if entry is None:
        raise ValueError(f"unknown Norway amendment source: {source_id}")

    blockers_by_current_law = _build_unresolved_blockers_by_current_law(index, current_law_ids=current_law_ids)
    current_law_base_ids = sorted(base_id for base_id in entry.base_ids if base_id in current_law_ids)
    executable_current_law_base_ids = sorted(
        base_id for base_id in current_law_base_ids if base_id in executable_current_law_ids
    )

    laws = []
    for base_id in current_law_base_ids:
        blockers = blockers_by_current_law.get(base_id, [])
        blocker_ids = [str(item["source_id"]) for item in blockers]
        laws.append(
            {
                "base_id": base_id,
                "title": current_law_titles.get(base_id, ""),
                "has_local_base_source": base_id in executable_current_law_ids,
                "blocker_count": len(blocker_ids),
                "sole_blocker": blocker_ids == [entry.source_id],
                "blocker_source_ids": blocker_ids,
            }
        )
    laws.sort(
        key=lambda item: (
            not bool(item["has_local_base_source"]),
            not bool(item["sole_blocker"]),
            -int(item["blocker_count"]),
            str(item["base_id"]),
        )
    )

    sole_blocker_current_laws = [item["base_id"] for item in laws if item["sole_blocker"]]
    sole_blocker_executable_current_laws = [
        item["base_id"] for item in laws if item["sole_blocker"] and item["has_local_base_source"]
    ]

    report = {
        "data_dir": str(data_dir),
        "source_id": entry.source_id,
        "normalized_phrase": normalize_no_commencement_phrase(entry.raw_date_in_force),
        "title": entry.title,
        "effective_status": entry.effective_status,
        "effective_date": entry.effective_date or "",
        "raw_date_in_force": entry.raw_date_in_force,
        "n_ops": entry.n_ops,
        "archive": entry.archive,
        "member_name": entry.member_name,
        "base_ids": list(entry.base_ids),
        "current_law_base_ids": current_law_base_ids,
        "executable_current_law_base_ids": executable_current_law_base_ids,
        "current_law_count": len(current_law_base_ids),
        "executable_current_law_count": len(executable_current_law_base_ids),
        "sole_blocker_current_laws": sole_blocker_current_laws,
        "sole_blocker_current_law_count": len(sole_blocker_current_laws),
        "sole_blocker_executable_current_laws": sole_blocker_executable_current_laws,
        "sole_blocker_executable_current_law_count": len(sole_blocker_executable_current_laws),
        "laws": laws,
    }
    report.update(no_override_state(entry.source_id, overrides))
    return report


def build_no_law_report(
    index: NOAmendmentIndex,
    *,
    base_id: str,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    from lawvm.norway.sources import iter_no_current_artifacts

    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir)
        if not current_law_ids:
            current_law_ids = {artifact.logical_id for artifact in iter_no_current_artifacts(data_dir)}
    if executable_current_law_ids is None:
        executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)

    entries = []
    statuses: list[str] = []
    for entry in index.entries_for_base(base_id):
        statuses.append(entry.effective_status)
        entries.append(
            {
                "source_id": entry.source_id,
                "title": entry.title,
                "effective_status": entry.effective_status,
                "effective_date": entry.effective_date or "",
                "raw_date_in_force": entry.raw_date_in_force,
                "n_ops": entry.n_ops,
                "archive": entry.archive,
                "member_name": entry.member_name,
            }
        )
    entries.sort(
        key=lambda item: (
            item["effective_status"] not in {"contingent", "missing", "unknown"},
            -int(item["n_ops"]),
            str(item["source_id"]),
        )
    )

    unresolved_entries = [item for item in entries if item["effective_status"] in {"contingent", "missing", "unknown"}]
    replay_status = _base_replay_status_from_statuses(statuses)
    if replay_status in {"blocked_contingent", "blocked_unknown"}:
        executable_replay_status = replay_status
    elif base_id not in current_law_ids:
        executable_replay_status = "not_current_law"
    elif base_id not in executable_current_law_ids:
        executable_replay_status = "missing_local_base_source"
    else:
        executable_replay_status = replay_status

    return {
        "data_dir": str(data_dir),
        "base_id": base_id,
        "title": current_law_titles.get(base_id, ""),
        "is_current_law": base_id in current_law_ids,
        "has_local_base_source": base_id in executable_current_law_ids,
        "amendment_count": len(entries),
        "replay_status": replay_status,
        "executable_replay_status": executable_replay_status,
        "blocking_count": len(unresolved_entries),
        "blocking_ops": sum(int(item["n_ops"]) for item in unresolved_entries),
        "sources": entries,
    }


def build_no_work_queue(
    index: NOAmendmentIndex,
    *,
    current_laws_only: bool = True,
    sort_mode: str = "unlock",
    phrase: str | None = None,
    override_state: str | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    laws_per_source: int = 5,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    from lawvm.norway.sources import iter_no_current_artifacts

    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir)
        if not current_law_ids:
            current_law_ids = {artifact.logical_id for artifact in iter_no_current_artifacts(data_dir)}
    if executable_current_law_ids is None:
        executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)

    report = build_no_commencement_report(
        index,
        current_law_ids=current_law_ids,
        current_laws_only=current_laws_only,
        phrase=phrase,
        override_state=override_state,
        overrides=overrides,
        sort_mode=sort_mode,
    )

    work_items = []
    override_counts: dict[str, int] = {}
    for item in report["entries"]:
        source_report = build_no_source_report(
            index,
            source_id=str(item["source_id"]),
            overrides=overrides,
            current_law_ids=current_law_ids,
            executable_current_law_ids=executable_current_law_ids,
            current_law_titles=current_law_titles,
        )
        laws = list(source_report["laws"])
        override_state = str(source_report["override_state"])
        override_counts[override_state] = override_counts.get(override_state, 0) + 1
        work_items.append(
            {
                "source_id": source_report["source_id"],
                "normalized_phrase": source_report["normalized_phrase"],
                "override_state": source_report["override_state"],
                "override_effective_date": source_report["override_effective_date"],
                "override_note": source_report["override_note"],
                "override_has_note": source_report["override_has_note"],
                "title": source_report["title"],
                "effective_status": source_report["effective_status"],
                "raw_date_in_force": source_report["raw_date_in_force"],
                "n_ops": source_report["n_ops"],
                "current_law_count": source_report["current_law_count"],
                "executable_current_law_count": source_report["executable_current_law_count"],
                "sole_blocker_current_law_count": source_report["sole_blocker_current_law_count"],
                "sole_blocker_current_laws": list(source_report["sole_blocker_current_laws"]),
                "sole_blocker_executable_current_law_count": (
                    source_report["sole_blocker_executable_current_law_count"]
                ),
                "sole_blocker_executable_current_laws": list(
                    source_report["sole_blocker_executable_current_laws"]
                ),
                "top_laws": laws[:laws_per_source],
                "top_sole_blocker_executable_laws": [
                    {
                        "base_id": law["base_id"],
                        "title": current_law_titles.get(str(law["base_id"]), ""),
                    }
                    for law in laws
                    if law["has_local_base_source"] and law["sole_blocker"]
                ][:laws_per_source],
                "archive": source_report["archive"],
                "member_name": source_report["member_name"],
            }
        )

    return {
        "data_dir": str(data_dir),
        "current_laws_only": current_laws_only,
        "phrase_filter": _normalize_phrase_filter(phrase),
        "override_state_filter": _normalize_override_state_filter(override_state),
        "sort_mode": sort_mode,
        "laws_per_source": laws_per_source,
        "unresolved_count": report["unresolved_count"],
        "unresolved_by_status": report["unresolved_by_status"],
        "override_state_counts": override_counts,
        "work_items": work_items,
    }


def build_no_commencement_candidate_artifact(
    report: dict[str, Any],
    *,
    data_dir: Path | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Wrap a candidate report in a serializable phase artifact envelope."""
    artifact = dict(report)
    artifact.update(
        {
            "jurisdiction": "no",
            "artifact_kind": "commencement_candidate_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            ),
            "input_locators": {
                "data_dir": str(data_dir) if data_dir is not None else "",
                "index_path": str(index_path) if index_path is not None else "",
            },
            "source_lanes": {
                "local_corpus": int(report.get("local_candidate_count", 0)),
                "statsrad": int(report.get("statsrad_candidate_count", 0)),
            },
        }
    )
    return artifact


def _recommend_no_backfill_lane(candidate_source_counts: dict[str, int]) -> str:
    local_count = int(candidate_source_counts.get("local_corpus", 0))
    statsrad_count = int(candidate_source_counts.get("statsrad", 0))
    if local_count and statsrad_count:
        return "mixed"
    if statsrad_count:
        return "statsrad"
    if local_count:
        return "local_corpus"
    return "unresolved"


def _build_no_backfill_action_hint(
    *,
    source_id: str,
    recommended_lane: str,
    candidate_groups: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if recommended_lane == "mixed":
        next_steps = [
            "Compare local_corpus and statsrad candidates side-by-side.",
            "Use the top excerpts to decide which source states the force-setting event most directly.",
        ]
    elif recommended_lane == "local_corpus":
        next_steps = [
            "Start with local_corpus candidates; the local law corpus appears to carry the useful force-setting signal.",
            "Use statsrad only as a fallback if the local excerpts are ambiguous.",
        ]
    elif recommended_lane == "statsrad":
        next_steps = [
            "Start with statsrad candidates; the local corpus has no obvious candidate match here.",
            "Look for explicit commencement wording and dates in the statsrad excerpts first.",
        ]
    else:
        next_steps = [
            "No local_corpus or statsrad candidate surfaced for this source.",
            "Search other official publication channels or handle this one manually.",
        ]

    candidate_snapshots: list[dict[str, Any]] = []
    for candidate in candidates[:3]:
        matches = list(candidate.get("matches", []))
        top_match = matches[0] if matches else {}
        candidate_snapshots.append(
            {
                "candidate_source": candidate.get("candidate_source", ""),
                "source_id": candidate.get("source_id", ""),
                "title": candidate.get("title", ""),
                "score": int(candidate.get("score", 0)),
                "commencement_marker": bool(candidate.get("commencement_marker", False)),
                "direct_match": bool(candidate.get("direct_match", False)),
                "top_match_kind": str(top_match.get("kind", "")),
                "top_match_needle": str(top_match.get("needle", "")),
                "top_match_excerpt": str(top_match.get("excerpt", "")),
            }
        )

    return {
        "source_id": source_id,
        "recommended_lane": recommended_lane,
        "next_steps": next_steps,
        "candidate_snapshots": candidate_snapshots,
        "candidate_group_summary": [
            {
                "candidate_source": str(group.get("candidate_source", "")),
                "candidate_count": int(group.get("candidate_count", 0)),
            }
            for group in candidate_groups
        ],
    }


def _build_no_backfill_next_source_hint(
    *,
    source_id: str,
    title: str,
    recommended_lane: str,
    candidate_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    if recommended_lane == "unresolved":
        return {
            "source_id": source_id,
            "title": title,
            "status": "needs_external_official_source",
            "kind": "external_official_search",
            "primary_source_family": "other_official_publication_channels",
            "suggested_sources": [
                "Offisielt fra statsråd on regjeringen.no",
                "ministerial regulations / delegated commencement decisions",
                "Lovdata Pro historical layers",
            ],
            "rationale": "No local_corpus or statsrad candidate surfaced for this source.",
            "candidate_group_summary": [
                {
                    "candidate_source": str(group.get("candidate_source", "")),
                    "candidate_count": int(group.get("candidate_count", 0)),
                }
                for group in candidate_groups
            ],
        }
    if recommended_lane == "mixed":
        return {
            "source_id": source_id,
            "title": title,
            "status": "compare_existing_lanes",
            "kind": "lane_comparison",
            "primary_source_family": "local_corpus_and_statsrad",
            "suggested_sources": [
                "Compare local_corpus and statsrad side-by-side before moving outward.",
            ],
            "rationale": "Both local_corpus and statsrad produced candidates for this source.",
            "candidate_group_summary": [
                {
                    "candidate_source": str(group.get("candidate_source", "")),
                    "candidate_count": int(group.get("candidate_count", 0)),
                }
                for group in candidate_groups
            ],
        }
    if recommended_lane == "statsrad":
        return {
            "source_id": source_id,
            "title": title,
            "status": "statsrad_first",
            "kind": "single_lane",
            "primary_source_family": "statsrad",
            "suggested_sources": [
                "Start with statsrad candidates; local_corpus has no obvious candidate match here.",
            ],
            "rationale": "Only statsrad candidates surfaced for this source.",
            "candidate_group_summary": [
                {
                    "candidate_source": str(group.get("candidate_source", "")),
                    "candidate_count": int(group.get("candidate_count", 0)),
                }
                for group in candidate_groups
            ],
        }
    return {
        "source_id": source_id,
        "title": title,
        "status": "local_corpus_first",
        "kind": "single_lane",
        "primary_source_family": "local_corpus",
        "suggested_sources": [
            "Start with local_corpus candidates; the local law corpus appears to carry the useful force-setting signal.",
        ],
        "rationale": "Only local_corpus candidates surfaced for this source.",
        "candidate_group_summary": [
            {
                "candidate_source": str(group.get("candidate_source", "")),
                "candidate_count": int(group.get("candidate_count", 0)),
            }
            for group in candidate_groups
        ],
    }


def _build_no_backfill_source_plan(
    *,
    source_id: str,
    title: str,
    recommended_lane: str,
    candidate_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if recommended_lane == "mixed":
        return [
            {
                "source_family": "local_corpus",
                "display_name": "local_corpus",
                "priority": 1,
                "mode": "compare",
                "status": "candidate",
                "why": "Local corpus candidates exist and should be compared with statsrad.",
                "candidate_group_summary": [
                    {
                        "candidate_source": str(group.get("candidate_source", "")),
                        "candidate_count": int(group.get("candidate_count", 0)),
                    }
                    for group in candidate_groups
                    if str(group.get("candidate_source", "")) == "local_corpus"
                ],
            },
            {
                "source_family": "statsrad",
                "display_name": "statsrad",
                "priority": 2,
                "mode": "compare",
                "status": "candidate",
                "why": "Statsrad candidates exist and should be compared against local corpus.",
                "candidate_group_summary": [
                    {
                        "candidate_source": str(group.get("candidate_source", "")),
                        "candidate_count": int(group.get("candidate_count", 0)),
                    }
                    for group in candidate_groups
                    if str(group.get("candidate_source", "")) == "statsrad"
                ],
            },
        ]
    if recommended_lane == "statsrad":
        return [
            {
                "source_family": "statsrad",
                "display_name": "statsrad",
                "priority": 1,
                "mode": "search",
                "status": "candidate",
                "why": "Statsrad has the only surfaced candidate signal.",
                "candidate_group_summary": [
                    {
                        "candidate_source": str(group.get("candidate_source", "")),
                        "candidate_count": int(group.get("candidate_count", 0)),
                    }
                    for group in candidate_groups
                    if str(group.get("candidate_source", "")) == "statsrad"
                ],
            }
        ]
    if recommended_lane == "local_corpus":
        return [
            {
                "source_family": "local_corpus",
                "display_name": "local_corpus",
                "priority": 1,
                "mode": "search",
                "status": "candidate",
                "why": "Local corpus has the surfaced candidate signal.",
                "candidate_group_summary": [
                    {
                        "candidate_source": str(group.get("candidate_source", "")),
                        "candidate_count": int(group.get("candidate_count", 0)),
                    }
                    for group in candidate_groups
                    if str(group.get("candidate_source", "")) == "local_corpus"
                ],
            }
        ]
    return [
        {
            "source_family": "offisielt_fra_statsrad",
            "display_name": "Offisielt fra statsråd",
            "priority": 1,
            "mode": "search",
            "status": "next_official_source",
            "why": "No local_corpus or statsrad candidate surfaced for this source.",
            "search_targets": [
                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                "sanction / commencement decisions in council-of-state bulletins",
            ],
        },
        {
            "source_family": "ministerial_regulations",
            "display_name": "ministerial regulations / delegated commencement decisions",
            "priority": 2,
            "mode": "search",
            "status": "next_official_source",
            "why": "Some contingent provisions are resolved by ministerial or delegated publication channels.",
            "search_targets": [
                "ministerial regulations",
                "delegated commencement decisions",
            ],
        },
        {
            "source_family": "lovdata_pro_history",
            "display_name": "Lovdata Pro historical layers",
            "priority": 3,
            "mode": "search",
            "status": "fallback_history",
            "why": "Deeper historical layers may contain the missing commencement context.",
            "search_targets": [
                "historical version / expression layer",
                "oldest accessible base or amendment chain",
            ],
        },
    ]


def _build_no_external_evidence_packets(
    *,
    next_source_plan: list[dict[str, Any]],
    source_id: str,
    title: str,
) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for plan_item in next_source_plan:
        if not isinstance(plan_item, dict):
            continue
        search_targets = [
            str(target)
            for target in plan_item.get("search_targets", [])
            if isinstance(target, str) and target.strip()
        ]
        packets.append(
            {
                "source_id": source_id,
                "title": title,
                "source_family": str(plan_item.get("source_family", "")),
                "display_name": str(plan_item.get("display_name", "")),
                "priority": int(plan_item.get("priority", 0)),
                "mode": str(plan_item.get("mode", "")),
                "status": str(plan_item.get("status", "")),
                "packet_note": str(plan_item.get("why", "")),
                "search_targets": search_targets,
            }
        )
    return packets


def build_no_commencement_backfill_artifact(
    index: NOAmendmentIndex,
    *,
    data_dir: Path | None = None,
    index_path: Path | None = None,
    current_laws_only: bool = True,
    sort_mode: str = "unlock",
    phrase: str | None = None,
    override_state: str | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    laws_per_source: int = 5,
    limit: int = 10,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Wrap a commencement work queue into a batchable evidence backfill artifact."""
    from lawvm.tools.no_commencement_candidates import build_no_commencement_candidate_report

    queue = build_no_work_queue(
        index,
        current_laws_only=current_laws_only,
        sort_mode=sort_mode,
        phrase=phrase,
        override_state=override_state,
        overrides=overrides,
        laws_per_source=laws_per_source,
        current_law_ids=current_law_ids,
        executable_current_law_ids=executable_current_law_ids,
        current_law_titles=current_law_titles,
    )
    source_counts = {"local_corpus": 0, "statsrad": 0}
    backfill_items: list[dict[str, Any]] = []
    for item in list(queue.get("work_items", []))[:limit]:
        source_id = str(item.get("source_id", ""))
        candidate_report = build_no_commencement_candidate_report(
            source_id=source_id,
            data_dir=data_dir,
            index_path=index_path,
            limit=laws_per_source,
            direct_only=False,
        )
        candidate_counts = dict(candidate_report.get("candidate_source_counts", {}))
        recommended_lane = _recommend_no_backfill_lane(candidate_counts)
        source_counts["local_corpus"] += int(candidate_counts.get("local_corpus", 0))
        source_counts["statsrad"] += int(candidate_counts.get("statsrad", 0))
        action_hint = _build_no_backfill_action_hint(
            source_id=source_id,
            recommended_lane=recommended_lane,
            candidate_groups=list(candidate_report.get("candidate_groups", [])),
            candidates=list(candidate_report.get("candidates", [])),
        )
        next_source_hint = _build_no_backfill_next_source_hint(
            source_id=source_id,
            title=str(item.get("title", "")),
            recommended_lane=recommended_lane,
            candidate_groups=list(candidate_report.get("candidate_groups", [])),
        )
        next_source_plan = _build_no_backfill_source_plan(
            source_id=source_id,
            title=str(item.get("title", "")),
            recommended_lane=recommended_lane,
            candidate_groups=list(candidate_report.get("candidate_groups", [])),
        )
        backfill_items.append(
            {
                "source_id": source_id,
                "title": item.get("title", ""),
                "normalized_phrase": item.get("normalized_phrase", ""),
                "override_state": item.get("override_state", ""),
                "override_effective_date": item.get("override_effective_date", ""),
                "current_law_count": int(item.get("current_law_count", 0)),
                "executable_current_law_count": int(item.get("executable_current_law_count", 0)),
                "sole_blocker_current_law_count": int(item.get("sole_blocker_current_law_count", 0)),
                "sole_blocker_executable_current_law_count": int(
                    item.get("sole_blocker_executable_current_law_count", 0)
                ),
                "candidate_source_counts": candidate_counts,
                "candidate_groups": list(candidate_report.get("candidate_groups", [])),
                "candidate_count": int(candidate_report.get("candidate_count", 0)),
                "recommended_lane": recommended_lane,
                "action_hint": action_hint,
                "next_source_hint": next_source_hint,
                "next_source_plan": next_source_plan,
                "top_laws": list(item.get("top_laws", [])),
            }
        )

    artifact = {
        "jurisdiction": "no",
        "artifact_kind": "commencement_backfill_artifact",
        "phase_owner": "lawvm.norway.commencement",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "input_locators": {
            "data_dir": str(data_dir) if data_dir is not None else "",
            "index_path": str(index_path) if index_path is not None else "",
        },
        "source_lanes": dict(source_counts),
        "work_queue": queue,
        "backfill_items": backfill_items,
    }
    return artifact


def build_no_commencement_external_evidence_plan_artifact(
    index: NOAmendmentIndex,
    *,
    data_dir: Path | None = None,
    index_path: Path | None = None,
    current_laws_only: bool = True,
    sort_mode: str = "unlock",
    phrase: str | None = None,
    override_state: str | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    laws_per_source: int = 5,
    limit: int = 10,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialize an unresolved-only external evidence plan for Norway commencement."""
    from lawvm.tools.no_commencement_candidates import build_no_commencement_candidate_report

    backfill = build_no_commencement_backfill_artifact(
        index,
        data_dir=data_dir,
        index_path=index_path,
        current_laws_only=current_laws_only,
        sort_mode=sort_mode,
        phrase=phrase,
        override_state=override_state,
        overrides=overrides,
        laws_per_source=laws_per_source,
        current_law_ids=current_law_ids,
        executable_current_law_ids=executable_current_law_ids,
        current_law_titles=current_law_titles,
    )

    unresolved_items: list[dict[str, Any]] = []
    external_source_family_counts: Counter[str] = Counter()
    for item in list(backfill.get("backfill_items", [])):
        source_id = str(item.get("source_id", ""))
        title = str(item.get("title", ""))
        recommended_lane = str(item.get("recommended_lane", ""))
        candidate_counts = dict(item.get("candidate_source_counts", {}))
        candidate_groups = list(item.get("candidate_groups", []))
        if not recommended_lane and candidate_counts and candidate_groups:
            recommended_lane = _recommend_no_backfill_lane(candidate_counts)
        elif not recommended_lane or not candidate_counts or not candidate_groups:
            candidate_report = build_no_commencement_candidate_report(
                source_id=source_id,
                data_dir=data_dir,
                index_path=index_path,
                limit=laws_per_source,
                direct_only=False,
            )
            candidate_counts = dict(candidate_report.get("candidate_source_counts", {}))
            candidate_groups = list(candidate_report.get("candidate_groups", []))
            recommended_lane = _recommend_no_backfill_lane(candidate_counts)
        if recommended_lane != "unresolved":
            continue
        next_source_plan = _build_no_backfill_source_plan(
            source_id=source_id,
            title=title,
            recommended_lane="unresolved",
            candidate_groups=[],
        )
        next_source_hint = _build_no_backfill_next_source_hint(
            source_id=source_id,
            title=title,
            recommended_lane="unresolved",
            candidate_groups=[],
        )
        action_hint = _build_no_backfill_action_hint(
            source_id=source_id,
            recommended_lane="unresolved",
            candidate_groups=[],
            candidates=[],
        )
        for plan_item in next_source_plan:
            source_family = str(plan_item.get("source_family", ""))
            if source_family:
                external_source_family_counts[source_family] += 1
        unresolved_items.append(
            {
                "source_id": source_id,
                "title": title,
                "normalized_phrase": item.get("normalized_phrase", ""),
                "override_state": item.get("override_state", ""),
                "override_effective_date": item.get("override_effective_date", ""),
                "current_law_count": int(item.get("current_law_count", 0)),
                "executable_current_law_count": int(item.get("executable_current_law_count", 0)),
                "sole_blocker_current_law_count": int(item.get("sole_blocker_current_law_count", 0)),
                "sole_blocker_executable_current_law_count": int(
                    item.get("sole_blocker_executable_current_law_count", 0)
                ),
                "candidate_source_counts": candidate_counts,
                "candidate_groups": candidate_groups,
                "candidate_count": int(sum(int(v) for v in candidate_counts.values())),
                "recommended_lane": "unresolved",
                "action_hint": action_hint,
                "next_source_hint": next_source_hint,
                "next_source_plan": next_source_plan,
                "source_packets": _build_no_external_evidence_packets(
                    next_source_plan=next_source_plan,
                    source_id=source_id,
                    title=title,
                ),
                "top_laws": list(item.get("top_laws", [])),
            }
        )
        if len(unresolved_items) >= limit:
            break

    artifact = {
        "jurisdiction": "no",
        "artifact_kind": "commencement_external_evidence_plan_artifact",
        "phase_owner": "lawvm.norway.commencement",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "input_locators": {
            "data_dir": str(data_dir) if data_dir is not None else "",
            "index_path": str(index_path) if index_path is not None else "",
        },
        "current_laws_only": current_laws_only,
        "sort_mode": sort_mode,
        "phrase_filter": _normalize_phrase_filter(phrase),
        "override_state_filter": _normalize_override_state_filter(override_state),
        "unresolved_count": len(unresolved_items),
        "external_source_family_counts": dict(sorted(external_source_family_counts.items())),
        "plan_items": unresolved_items,
        "queue": backfill.get("work_queue", {}),
    }
    return artifact


def build_no_commencement_phrase_report(
    index: NOAmendmentIndex,
    *,
    current_laws_only: bool = True,
    phrase: str | None = None,
    override_state: str | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    sort_mode: str = "unlock",
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    from lawvm.norway.sources import iter_no_current_artifacts

    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir)
        if not current_law_ids:
            current_law_ids = {artifact.logical_id for artifact in iter_no_current_artifacts(data_dir)}
    if executable_current_law_ids is None:
        executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)
    phrase_filter = _normalize_phrase_filter(phrase)
    override_state_filter = _normalize_override_state_filter(override_state)

    unresolved_entries = [
        entry
        for entry in index.entries
        if entry.effective_status in {"contingent", "missing", "unknown"}
        and (not phrase_filter or normalize_no_commencement_phrase(entry.raw_date_in_force) == phrase_filter)
    ]

    phrase_groups: dict[str, dict[str, Any]] = {}
    for entry in unresolved_entries:
        source_report = build_no_source_report(
            index,
            source_id=entry.source_id,
            overrides=overrides,
            current_law_ids=current_law_ids,
            executable_current_law_ids=executable_current_law_ids,
            current_law_titles=current_law_titles,
        )
        if current_laws_only and not source_report["current_law_count"]:
            continue
        if override_state_filter and source_report["override_state"] != override_state_filter:
            continue

        phrase = normalize_no_commencement_phrase(entry.raw_date_in_force)
        group = phrase_groups.setdefault(
            phrase,
            {
                "phrase": phrase,
                "raw_examples": [],
                "source_ids": [],
                "current_law_ids": set(),
                "executable_current_law_ids": set(),
                "sole_blocker_current_law_ids": set(),
                "sole_blocker_executable_current_law_ids": set(),
                "n_ops": 0,
                "top_sources": [],
                "override_state_counts": {},
            },
        )
        raw_text = entry.raw_date_in_force.strip()
        if raw_text and raw_text not in group["raw_examples"]:
            group["raw_examples"].append(raw_text)
        group["source_ids"].append(entry.source_id)
        group["current_law_ids"].update(source_report["current_law_base_ids"])
        group["executable_current_law_ids"].update(source_report["executable_current_law_base_ids"])
        group["sole_blocker_current_law_ids"].update(source_report["sole_blocker_current_laws"])
        group["sole_blocker_executable_current_law_ids"].update(
            source_report["sole_blocker_executable_current_laws"]
        )
        group["n_ops"] += int(entry.n_ops)
        group["top_sources"].append(
            {
                "source_id": entry.source_id,
                "override_state": source_report["override_state"],
                "override_effective_date": source_report["override_effective_date"],
                "title": entry.title,
                "current_law_count": int(source_report["current_law_count"]),
                "executable_current_law_count": int(source_report["executable_current_law_count"]),
                "sole_blocker_executable_current_law_count": int(
                    source_report["sole_blocker_executable_current_law_count"]
                ),
                "n_ops": int(entry.n_ops),
            }
        )
        override_state = str(source_report["override_state"])
        override_counts = group["override_state_counts"]
        override_counts[override_state] = int(override_counts.get(override_state, 0)) + 1

    groups = []
    for group in phrase_groups.values():
        groups.append(
            {
                "phrase": str(group["phrase"]),
                "raw_examples": list(group["raw_examples"]),
                "source_ids": sorted(group["source_ids"]),
                "source_count": len(group["source_ids"]),
                "current_law_count": len(group["current_law_ids"]),
                "executable_current_law_count": len(group["executable_current_law_ids"]),
                "sole_blocker_current_law_count": len(group["sole_blocker_current_law_ids"]),
                "sole_blocker_executable_current_law_count": len(
                    group["sole_blocker_executable_current_law_ids"]
                ),
                "n_ops": int(group["n_ops"]),
                "override_state_counts": dict(group["override_state_counts"]),
                "top_sources": sorted(
                    group["top_sources"],
                    key=lambda item: (
                        -int(item["sole_blocker_executable_current_law_count"]),
                        -int(item["executable_current_law_count"]),
                        -int(item["n_ops"]),
                        str(item["source_id"]),
                    ),
                )[:5],
            }
        )

    if sort_mode == "unlock":
        groups.sort(
            key=lambda item: (
                -int(item["sole_blocker_executable_current_law_count"]),
                -int(item["executable_current_law_count"]),
                -int(item["source_count"]),
                str(item["phrase"]),
            )
        )
    elif sort_mode == "impact":
        groups.sort(
            key=lambda item: (
                -int(item["executable_current_law_count"]),
                -int(item["current_law_count"]),
                -int(item["source_count"]),
                str(item["phrase"]),
            )
        )
    else:
        groups.sort(key=lambda item: str(item["phrase"]))

    return {
        "data_dir": str(data_dir),
        "current_laws_only": current_laws_only,
        "phrase_filter": phrase_filter,
        "override_state_filter": override_state_filter,
        "sort_mode": sort_mode,
        "phrase_count": len(groups),
        "groups": groups,
    }


def build_no_progress_report(
    index: NOAmendmentIndex,
    *,
    overrides: dict[str, dict[str, str]] | None = None,
    limit: int = 5,
    current_law_ids: set[str] | None = None,
    executable_current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir)
    if executable_current_law_ids is None:
        executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)
    queue = build_no_work_queue(
        index,
        current_laws_only=True,
        sort_mode="unlock",
        overrides=overrides,
        laws_per_source=3,
        current_law_ids=current_law_ids,
        executable_current_law_ids=executable_current_law_ids,
        current_law_titles=current_law_titles,
    )
    phrase_report = build_no_commencement_phrase_report(
        index,
        current_laws_only=True,
        overrides=overrides,
        sort_mode="unlock",
        current_law_ids=current_law_ids,
        executable_current_law_ids=executable_current_law_ids,
        current_law_titles=current_law_titles,
    )

    return {
        "data_dir": queue["data_dir"],
        "unresolved_count": queue["unresolved_count"],
        "override_state_counts": dict(queue["override_state_counts"]),
        "phrase_count": int(phrase_report["phrase_count"]),
        "blank_work_items": [
            item for item in queue["work_items"] if item["override_state"] == "blank"
        ][:limit],
        "untracked_work_items": [
            item for item in queue["work_items"] if item["override_state"] == "untracked"
        ][:limit],
        "phrase_groups": list(phrase_report["groups"])[:limit],
    }


def export_no_work_queue_packets(report: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    written = [summary_path]
    for index, item in enumerate(report.get("work_items", []), start=1):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id", "unknown"))
        slug = source_id.replace("/", "__")
        path = output_dir / f"{index:03d}_{slug}.json"
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def export_no_progress_packets(
    progress_report: dict[str, Any],
    *,
    blank_report: dict[str, Any],
    untracked_report: dict[str, Any],
    phrase_report: dict[str, Any],
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(progress_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    phrase_path = output_dir / "phrase_summary.json"
    phrase_path.write_text(
        json.dumps(phrase_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    written = [summary_path, phrase_path]
    written.extend(export_no_work_queue_packets(blank_report, output_dir / "blank"))
    written.extend(export_no_work_queue_packets(untracked_report, output_dir / "untracked"))
    return written


def build_no_blocked_law_report(
    index: NOAmendmentIndex,
    *,
    current_law_ids: set[str] | None = None,
    current_law_titles: dict[str, str] | None = None,
    base_id: str | None = None,
    min_blockers: int = 1,
) -> dict[str, Any]:
    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else None)
    base_id_filter = base_id
    if current_law_ids is None:
        current_law_ids = load_no_current_law_ids(data_dir) & load_available_lti_law_ids(data_dir)
    if current_law_titles is None:
        current_law_titles = load_no_current_law_titles(data_dir)

    blockers: dict[str, list[dict[str, Any]]] = {}
    for entry in index.entries:
        if entry.effective_status not in {"contingent", "missing", "unknown"}:
            continue
        affected = [candidate for candidate in entry.base_ids if candidate in current_law_ids]
        if base_id_filter is not None:
            affected = [candidate for candidate in affected if candidate == base_id_filter]
        if not affected:
            continue
        blocker_item = {
            "source_id": entry.source_id,
            "title": entry.title,
            "effective_status": entry.effective_status,
            "raw_date_in_force": entry.raw_date_in_force,
            "n_ops": entry.n_ops,
        }
        for base_id in affected:
            blockers.setdefault(base_id, []).append(blocker_item)

    laws = []
    for base_id, items in blockers.items():
        if len(items) < min_blockers:
            continue
        items_sorted = sorted(items, key=lambda item: (-int(item["n_ops"]), str(item["source_id"])))
        laws.append(
            {
                "base_id": base_id,
                "title": current_law_titles.get(base_id, ""),
                "blocking_count": len(items_sorted),
                "blocking_ops": sum(int(item["n_ops"]) for item in items_sorted),
                "blockers": items_sorted,
            }
        )
    laws.sort(key=lambda item: (-int(item["blocking_count"]), -int(item["blocking_ops"]), str(item["base_id"])))
    return {
        "data_dir": str(data_dir),
        "current_laws": len(current_law_ids),
        "blocked_law_count": len(laws),
        "base_id_filter": base_id_filter or "",
        "min_blockers": min_blockers,
        "laws": laws,
    }


def build_no_override_impact_report(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_fully = int(before.get("current_laws_fully_replayable", 0))
    after_fully = int(after.get("current_laws_fully_replayable", 0))
    before_blocked = int(before.get("current_laws_blocked_contingent", 0))
    after_blocked = int(after.get("current_laws_blocked_contingent", 0))
    before_exec_fully = int(
        before.get("current_laws_with_amendments_fully_replayable_executable", before_fully)
    )
    after_exec_fully = int(
        after.get("current_laws_with_amendments_fully_replayable_executable", after_fully)
    )
    before_exec_blocked = int(
        before.get("current_laws_with_amendments_blocked_contingent_executable", before_blocked)
    )
    after_exec_blocked = int(
        after.get("current_laws_with_amendments_blocked_contingent_executable", after_blocked)
    )
    before_status = before.get("amendment_documents_by_status", {})
    after_status = after.get("amendment_documents_by_status", {})
    return {
        "current_laws_fully_replayable_before": before_fully,
        "current_laws_fully_replayable_after": after_fully,
        "current_laws_fully_replayable_delta": after_fully - before_fully,
        "current_laws_blocked_contingent_before": before_blocked,
        "current_laws_blocked_contingent_after": after_blocked,
        "current_laws_blocked_contingent_delta": after_blocked - before_blocked,
        "current_laws_with_amendments_fully_replayable_executable_before": before_exec_fully,
        "current_laws_with_amendments_fully_replayable_executable_after": after_exec_fully,
        "current_laws_with_amendments_fully_replayable_executable_delta": (
            after_exec_fully - before_exec_fully
        ),
        "current_laws_with_amendments_blocked_contingent_executable_before": before_exec_blocked,
        "current_laws_with_amendments_blocked_contingent_executable_after": after_exec_blocked,
        "current_laws_with_amendments_blocked_contingent_executable_delta": (
            after_exec_blocked - before_exec_blocked
        ),
        "amendment_status_before": before_status,
        "amendment_status_after": after_status,
    }


def validate_no_commencement_overrides(
    index: NOAmendmentIndex,
    overrides: dict[str, dict[str, str]],
) -> dict[str, Any]:
    index_by_source = {entry.source_id: entry for entry in index.entries}
    invalid_date_format = []
    unknown_source_ids = []
    redundant_sources = []
    resolvable_sources = []
    blank_effective_date = []
    resolved_with_evidence = []
    resolved_missing_evidence = []

    for source_id, payload in overrides.items():
        effective_date = payload.get("effective_date", "")
        resolution_kind = payload.get("resolution_kind", "").strip()
        evidence_source_id = payload.get("evidence_source_id", "").strip()
        evidence_excerpt = payload.get("evidence_excerpt", "").strip()
        entry = index_by_source.get(source_id)
        if not effective_date:
            blank_effective_date.append(source_id)
            if entry is None:
                continue
            continue
        if entry is None:
            unknown_source_ids.append(source_id)
            continue
        if not _ISO_DATE_RE.match(effective_date):
            invalid_date_format.append(source_id)
            continue
        has_evidence = bool(resolution_kind or evidence_source_id or evidence_excerpt)
        if has_evidence:
            resolved_with_evidence.append(source_id)
        else:
            resolved_missing_evidence.append(source_id)
        if entry.effective_status in {"dated", "immediate", "override"}:
            redundant_sources.append(source_id)
        else:
            resolvable_sources.append(source_id)

    missing_contingent_sources = sorted(
        entry.source_id
        for entry in index.entries
        if entry.effective_status == "contingent" and entry.source_id not in overrides
    )

    return {
        "override_count": len(overrides),
        "resolvable_sources": sorted(resolvable_sources),
        "unknown_source_ids": sorted(unknown_source_ids),
        "invalid_date_format": sorted(invalid_date_format),
        "redundant_sources": sorted(redundant_sources),
        "blank_effective_date": sorted(blank_effective_date),
        "resolved_with_evidence": sorted(resolved_with_evidence),
        "resolved_missing_evidence": sorted(resolved_missing_evidence),
        "missing_contingent_sources": missing_contingent_sources,
    }
