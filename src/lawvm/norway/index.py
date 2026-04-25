"""Index Norway amendment sources into replayable metadata."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, cast

from lawvm.norway.grafter import iter_no_document_change_ops, lovdata_amendment_filename_to_id
from lawvm.norway.sources import (
    effective_date_from_amendment,
    iter_no_amendment_artifacts,
    no_source_metadata,
    parse_header_value,
    resolve_no_source_path,
)


@dataclass(frozen=True)
class NOAmendmentIndexEntry:
    source_id: str
    archive: str
    member_name: str
    effective_status: str
    effective_date: Optional[str] = None
    raw_date_in_force: str = ""
    title: str = ""
    base_ids: tuple[str, ...] = ()
    n_ops: int = 0


@dataclass
class NOAmendmentIndex:
    data_dir: str
    source_kind: str = "dir"
    generated_at_utc: str = ""
    archive_names: list[str] = field(default_factory=list)
    archive_metadata: dict[str, dict[str, int | str]] = field(default_factory=dict)
    entries: list[NOAmendmentIndexEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_dir": self.data_dir,
            "source_kind": self.source_kind,
            "generated_at_utc": self.generated_at_utc,
            "archive_names": list(self.archive_names),
            "archive_metadata": self.archive_metadata,
            "entries": [asdict(entry) for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NOAmendmentIndex":
        raw_entries = data.get("entries", [])
        entries = [
            NOAmendmentIndexEntry(
                source_id=entry["source_id"],
                archive=entry["archive"],
                member_name=entry["member_name"],
                effective_status=entry["effective_status"],
                effective_date=entry.get("effective_date"),
                raw_date_in_force=entry.get("raw_date_in_force", ""),
                title=entry.get("title", ""),
                base_ids=tuple(entry.get("base_ids", [])),
                n_ops=int(entry.get("n_ops", 0)),
            )
            for entry in raw_entries
            if isinstance(entry, dict)
        ]
        archive_names = [str(item) for item in data.get("archive_names", [])]
        archive_metadata = data.get("archive_metadata", {})
        return cls(
            data_dir=str(data.get("data_dir", "")),
            source_kind=str(data.get("source_kind", "dir")),
            generated_at_utc=str(data.get("generated_at_utc", "")),
            archive_names=archive_names,
            archive_metadata={
                str(key): value for key, value in archive_metadata.items()
                if isinstance(key, str) and isinstance(value, dict)
            },
            entries=entries,
        )

    def entries_for_base(self, base_id: str) -> list[NOAmendmentIndexEntry]:
        return [entry for entry in self.entries if base_id in entry.base_ids]

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.effective_status] = counts.get(entry.effective_status, 0) + 1
        return counts

    def staleness_report(self, data_dir: Optional[Path] = None) -> dict[str, object]:
        data_dir = resolve_no_source_path(data_dir or Path(self.data_dir))
        if self.source_kind == "farchive":
            if not data_dir.exists():
                return {
                    "index_stale": True,
                    "missing_archives": [str(data_dir)],
                    "stale_archives": [],
                }
            stat = data_dir.stat()
            current = {
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            recorded = self.archive_metadata.get("__farchive__", {})
            expected = {
                "size": int(recorded.get("size", -1)),
                "mtime_ns": int(recorded.get("mtime_ns", -1)),
            }
            return {
                "index_stale": current != expected,
                "missing_archives": [],
                "stale_archives": [] if current == expected else [{"archive": str(data_dir), "recorded": expected, "current": current}],
            }
        stale_archives = []
        missing_archives = []
        for archive_name in self.archive_names:
            path = data_dir / archive_name
            meta = self.archive_metadata.get(archive_name, {})
            if not path.exists():
                missing_archives.append(archive_name)
                continue
            stat = path.stat()
            current = {
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            recorded = {
                "size": int(meta.get("size", -1)),
                "mtime_ns": int(meta.get("mtime_ns", -1)),
            }
            if current != recorded:
                stale_archives.append(
                    {
                        "archive": archive_name,
                        "recorded": recorded,
                        "current": current,
                    }
                )
        return {
            "index_stale": bool(stale_archives or missing_archives),
            "missing_archives": missing_archives,
            "stale_archives": stale_archives,
        }


def build_no_amendment_index(data_dir: Optional[Path] = None) -> NOAmendmentIndex:
    data_dir = resolve_no_source_path(data_dir)
    source_meta = no_source_metadata(data_dir)
    archive_names = [str(item) for item in source_meta.get("archive_names", [])]
    archive_metadata: dict[str, dict[str, int | str]] = {}
    raw_archive_metadata = source_meta.get("archive_metadata", {})
    if isinstance(raw_archive_metadata, dict):
        archive_metadata = cast(dict[str, dict[str, int | str]], raw_archive_metadata)
    if source_meta.get("source_kind") == "farchive" and source_meta.get("exists"):
        archive_metadata = {
            "__farchive__": {
                "size": int(source_meta.get("size", 0)),
                "mtime_ns": int(source_meta.get("mtime_ns", 0)),
            }
        }
    index = NOAmendmentIndex(
        data_dir=str(data_dir),
        source_kind=str(source_meta.get("source_kind", "dir")),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        archive_names=archive_names,
        archive_metadata=archive_metadata,
    )

    for artifact in iter_no_amendment_artifacts(data_dir):
        source_id = artifact.logical_id
        if lovdata_amendment_filename_to_id(artifact.member_name) is None and not artifact.locator.startswith("no://lovtid/"):
            continue
        grouped = iter_no_document_change_ops(artifact.payload, source_id)
        if not grouped:
            continue
        effective = effective_date_from_amendment(
            artifact.payload,
            source_date=source_id.removeprefix("no/lovtid/"),
        )
        index.entries.append(
            NOAmendmentIndexEntry(
                source_id=source_id,
                archive=artifact.source_name,
                member_name=artifact.member_name,
                effective_status=effective.status,
                effective_date=effective.effective_date,
                raw_date_in_force=effective.raw_text,
                title=parse_header_value(artifact.payload, "title") or parse_header_value(artifact.payload, "titleShort"),
                base_ids=tuple(sorted({base_id for base_id, _ops in grouped})),
                n_ops=sum(len(ops) for _base_id, ops in grouped),
            )
        )

    index.entries.sort(key=lambda entry: (entry.source_id, entry.archive, entry.member_name))
    return index


def load_no_amendment_index(path: Path) -> NOAmendmentIndex:
    return NOAmendmentIndex.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_no_amendment_index(index: NOAmendmentIndex, path: Path) -> None:
    path.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
