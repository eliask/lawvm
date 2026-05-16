"""Version-to-version structural diffs for archived NZ XML.

This is an oracle/witness comparison layer, not amendment replay. It compares
two parsed consolidated XML versions and reports which source paths were added,
removed, or changed. Later replay work can use this to approximate effect
frontiers before lowering amendment Acts.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work
from lawvm.new_zealand.source_tree import NZSourceDocument, parse_nz_source_document


@dataclass(frozen=True)
class NZNodeChange:
    path: tuple[str, ...]
    change_type: str
    before_xml_id: str = ""
    after_xml_id: str = ""
    before_heading: str = ""
    after_heading: str = ""

    def to_jsonable(self) -> dict[str, object]:
        return {
            "path": list(self.path),
            "change_type": self.change_type,
            "before_xml_id": self.before_xml_id,
            "after_xml_id": self.after_xml_id,
            "before_heading": self.before_heading,
            "after_heading": self.after_heading,
        }


@dataclass(frozen=True)
class NZArchivedVersion:
    """One archived consolidated XML witness for a NZ work.

    ``version_date`` is a publication/version suffix from the API ``version_id``.
    It is not an effective-date or commencement claim.
    """

    version_id: str
    xml_locator: str
    version_date: str = ""

    def to_jsonable(self) -> dict[str, object]:
        return {
            "version_id": self.version_id,
            "xml_locator": self.xml_locator,
            "version_date": self.version_date,
        }


@dataclass(frozen=True)
class NZArchivedVersionDateWindow:
    work_id: str
    requested_version_date: str
    on_or_before: NZArchivedVersion | None
    on_or_after: NZArchivedVersion | None
    rule_id: str = "nz_archived_xml_version_date_window_source_only"
    truth_claim: str = "source_version_date_window_not_effective_date"

    def to_jsonable(self) -> dict[str, object]:
        return {
            "work_id": self.work_id,
            "requested_version_date": self.requested_version_date,
            "rule_id": self.rule_id,
            "truth_claim": self.truth_claim,
            "replay_claims": False,
            "on_or_before": self.on_or_before.to_jsonable() if self.on_or_before else None,
            "on_or_after": self.on_or_after.to_jsonable() if self.on_or_after else None,
        }


@dataclass(frozen=True)
class NZArchivedVersionChangeWindow:
    work_id: str
    requested_version_date: str
    before: NZArchivedVersion | None
    on_or_after: NZArchivedVersion | None
    rule_id: str = "nz_archived_xml_version_change_window_source_only"
    truth_claim: str = "source_change_window_not_effective_date"

    def to_jsonable(self) -> dict[str, object]:
        return {
            "work_id": self.work_id,
            "requested_version_date": self.requested_version_date,
            "rule_id": self.rule_id,
            "truth_claim": self.truth_claim,
            "replay_claims": False,
            "before": self.before.to_jsonable() if self.before else None,
            "on_or_after": self.on_or_after.to_jsonable() if self.on_or_after else None,
        }


@dataclass(frozen=True)
class NZVersionDiff:
    before_version_id: str
    after_version_id: str
    before_xml_locator: str
    after_xml_locator: str
    changes: tuple[NZNodeChange, ...]

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.change_type] = counts.get(change.change_type, 0) + 1
        return {
            "before_version_id": self.before_version_id,
            "after_version_id": self.after_version_id,
            "before_xml_locator": self.before_xml_locator,
            "after_xml_locator": self.after_xml_locator,
            "changes": len(self.changes),
            "change_counts": counts,
        }

    def to_jsonable(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "changes": [change.to_jsonable() for change in self.changes],
        }


def diff_source_documents(
    before: NZSourceDocument,
    after: NZSourceDocument,
) -> NZVersionDiff:
    before_nodes = _node_index(before)
    after_nodes = _node_index(after)
    changes: list[NZNodeChange] = []
    for path in sorted(before_nodes.keys() | after_nodes.keys()):
        before_node = before_nodes.get(path)
        after_node = after_nodes.get(path)
        if before_node is None and after_node is not None:
            changes.append(
                NZNodeChange(
                    path=path,
                    change_type="added",
                    after_xml_id=after_node.xml_id,
                    after_heading=after_node.heading,
                )
            )
        elif before_node is not None and after_node is None:
            changes.append(
                NZNodeChange(
                    path=path,
                    change_type="removed",
                    before_xml_id=before_node.xml_id,
                    before_heading=before_node.heading,
                )
            )
        elif before_node is not None and after_node is not None and _node_changed(before_node, after_node):
            changes.append(
                NZNodeChange(
                    path=path,
                    change_type="changed",
                    before_xml_id=before_node.xml_id,
                    after_xml_id=after_node.xml_id,
                    before_heading=before_node.heading,
                    after_heading=after_node.heading,
                )
            )
    return NZVersionDiff(
        before_version_id=before.version_id,
        after_version_id=after.version_id,
        before_xml_locator=before.xml_locator,
        after_xml_locator=after.xml_locator,
        changes=tuple(changes),
    )


def diff_archived_versions(
    *,
    db_path: Path,
    work_id: str,
    before_version_id: str = "",
    after_version_id: str = "",
) -> NZVersionDiff:
    archive = open_farchive(db_path)
    try:
        if not after_version_id:
            after_version_id, after_locator = latest_xml_locator_for_work(archive, work_id)
        else:
            after_locator = _xml_locator_for_version(archive, after_version_id)
        if not before_version_id:
            before_version_id, before_locator = _previous_xml_version_for_work(
                archive,
                work_id=work_id,
                after_version_id=after_version_id,
            )
        else:
            before_locator = _xml_locator_for_version(archive, before_version_id)
        before_bytes = archive.get(before_locator) if before_locator else None
        after_bytes = archive.get(after_locator) if after_locator else None
    finally:
        archive.close()
    if not before_version_id or not before_locator or before_bytes is None:
        raise RuntimeError("before XML version is not archived")
    if not after_version_id or not after_locator or after_bytes is None:
        raise RuntimeError("after XML version is not archived")
    return diff_source_documents(
        parse_nz_source_document(before_bytes, xml_locator=before_locator, version_id=before_version_id),
        parse_nz_source_document(after_bytes, xml_locator=after_locator, version_id=after_version_id),
    )


def _node_changed(before: Any, after: Any) -> bool:
    return (
        before.heading != after.heading
        or before.deletion_status != after.deletion_status
        or before.text != after.text
        or tuple(w.text for w in before.history) != tuple(w.text for w in after.history)
    )


def _node_index(document: NZSourceDocument) -> dict[tuple[str, ...], Any]:
    path_counts: Counter[tuple[str, ...]] = Counter(node.path for node in document.nodes)
    seen: Counter[tuple[str, ...]] = Counter()
    indexed: dict[tuple[str, ...], Any] = {}
    for node in document.nodes:
        if path_counts[node.path] == 1:
            key = node.path
        else:
            seen[node.path] += 1
            suffix = node.xml_id or f"ordinal:{seen[node.path]}"
            key = (*node.path, f"source-duplicate:{suffix}")
        indexed[key] = node
    return indexed


def _previous_xml_version_for_work(
    archive: Any,
    *,
    work_id: str,
    after_version_id: str,
) -> tuple[str, str]:
    previous = previous_archived_xml_version_for_work(
        archive,
        work_id=work_id,
        after_version_id=after_version_id,
    )
    if previous is None:
        return "", ""
    return previous.version_id, previous.xml_locator


def previous_archived_xml_version_for_work(
    archive: Any,
    *,
    work_id: str,
    after_version_id: str,
) -> NZArchivedVersion | None:
    """Return the preceding archived XML witness for a version.

    This uses archive version inventory order only. It is not a legal temporal
    precedence rule and must not be treated as amendment-effect proof.
    """

    versions = archived_xml_versions_for_work(archive, work_id)
    if not versions:
        return None
    for index, version in enumerate(versions):
        if version.version_id == after_version_id and index + 1 < len(versions):
            return versions[index + 1]
    if len(versions) > 1:
        return versions[1]
    return None


def archived_xml_versions_for_work(archive: Any, work_id: str) -> tuple[NZArchivedVersion, ...]:
    """Return newest-first archived XML version witnesses for ``work_id``.

    This is an archive/source inventory. It deliberately does not infer legal
    effect dates, target validity, or replay correctness from version order.
    """

    prefix = f"https://api.legislation.govt.nz/v0/versions/{work_id}_en_"
    versions: list[NZArchivedVersion] = []
    for detail_locator in archive.locators(prefix + "%"):
        version_id = detail_locator.rstrip("/").rsplit("/", 1)[-1]
        xml_locator = _xml_locator_for_version(archive, version_id)
        if xml_locator and archive.get(xml_locator) is not None:
            versions.append(
                NZArchivedVersion(
                    version_id=version_id,
                    xml_locator=xml_locator,
                    version_date=_version_date_from_version_id(version_id),
                )
            )
    return tuple(sorted(versions, key=_archived_version_sort_key, reverse=True))


def archived_xml_version_date_window(
    archive: Any,
    *,
    work_id: str,
    version_date: str,
) -> NZArchivedVersionDateWindow:
    """Find archived XML witnesses bracketing a source version-date.

    ``version_date`` must be an ISO ``YYYY-MM-DD`` date. Returned witnesses are
    selected by API version suffix only; this is not a commencement/effect
    selector and must not be used as legal replay proof by itself.
    """

    requested = _iso_date_prefix(version_date)
    on_or_before: NZArchivedVersion | None = None
    on_or_after: NZArchivedVersion | None = None
    dated_versions = tuple(
        (date_prefix, version)
        for version in archived_xml_versions_for_work(archive, work_id)
        if (date_prefix := _iso_date_prefix(version.version_date))
    )
    for date_prefix, version in dated_versions:
        if date_prefix <= requested:
            on_or_before = version
            break
    for date_prefix, version in reversed(dated_versions):
        if date_prefix >= requested:
            on_or_after = version
            break
    return NZArchivedVersionDateWindow(
        work_id=work_id,
        requested_version_date=requested,
        on_or_before=on_or_before,
        on_or_after=on_or_after,
    )


def archived_xml_version_change_window(
    archive: Any,
    *,
    work_id: str,
    version_date: str,
) -> NZArchivedVersionChangeWindow:
    """Find strict-before and on-or-after XML witnesses for change evidence.

    This is useful for source text-change investigation where an exact version
    dated to the amendment cannot serve as both pre- and post-change witness.
    It is still not a commencement/effect selector or replay proof.
    """

    requested = _iso_date_prefix(version_date)
    before: NZArchivedVersion | None = None
    on_or_after: NZArchivedVersion | None = None
    dated_versions = tuple(
        (date_prefix, version)
        for version in archived_xml_versions_for_work(archive, work_id)
        if (date_prefix := _iso_date_prefix(version.version_date))
    )
    for date_prefix, version in dated_versions:
        if date_prefix < requested:
            before = version
            break
    for date_prefix, version in reversed(dated_versions):
        if date_prefix >= requested:
            on_or_after = version
            break
    return NZArchivedVersionChangeWindow(
        work_id=work_id,
        requested_version_date=requested,
        before=before,
        on_or_after=on_or_after,
    )


def _archived_xml_versions_for_work(archive: Any, work_id: str) -> list[tuple[str, str]]:
    return [(version.version_id, version.xml_locator) for version in archived_xml_versions_for_work(archive, work_id)]


def _xml_locator_for_version(archive: Any, version_id: str) -> str:
    detail_locator = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    data = archive.get(detail_locator)
    if data is None:
        return ""
    try:
        detail = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return ""
    version_date = version_id.rsplit("_", 1)[-1] if "_" in version_id else ""
    formats = detail.get("formats")
    if not isinstance(formats, list):
        return ""
    for row in formats:
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("url") or "")
        kind = str(row.get("type") or row.get("format") or "").lower()
        if kind == "xml" or url.endswith(".xml"):
            return url.replace("/latest.xml", f"/{version_date}.xml")
    return ""


def _archived_version_sort_key(version: NZArchivedVersion) -> tuple[str, str]:
    return version.version_date, version.version_id


def _version_date_from_version_id(version_id: str) -> str:
    return version_id.rsplit("_en_", 1)[-1] if "_en_" in version_id else ""


def _iso_date_prefix(value: str) -> str:
    match = _ISO_DATE_PREFIX_RE.match(value.strip())
    return match.group(1) if match else ""


_ISO_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def main(args: Any) -> None:
    if args.list_versions:
        archive = open_farchive(Path(args.db))
        try:
            versions = archived_xml_versions_for_work(archive, args.work_id)
            date_window = (
                archived_xml_version_date_window(
                    archive,
                    work_id=args.work_id,
                    version_date=args.version_date,
                )
                if args.version_date
                else None
            )
            change_window = (
                archived_xml_version_change_window(
                    archive,
                    work_id=args.work_id,
                    version_date=args.version_date,
                )
                if args.version_date and args.change_window
                else None
            )
        finally:
            archive.close()
        if args.json:
            print(
                json.dumps(
                    {
                        "jurisdiction": "nz",
                        "report_kind": "archived_xml_version_inventory",
                        "truth_claim": "source_witness_inventory",
                        "replay_claims": False,
                        "work_id": args.work_id,
                        "version_date_window": date_window.to_jsonable() if date_window else None,
                        "version_change_window": change_window.to_jsonable() if change_window else None,
                        "versions": [version.to_jsonable() for version in versions],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        print(f"work_id={args.work_id} archived_xml_versions={len(versions)}")
        if date_window is not None:
            before = date_window.on_or_before.version_id if date_window.on_or_before else "-"
            after = date_window.on_or_after.version_id if date_window.on_or_after else "-"
            print(
                f"version_date_window={date_window.requested_version_date} "
                f"on_or_before={before} on_or_after={after} "
                f"truth_claim={date_window.truth_claim}"
            )
        if change_window is not None:
            before = change_window.before.version_id if change_window.before else "-"
            after = change_window.on_or_after.version_id if change_window.on_or_after else "-"
            print(
                f"version_change_window={change_window.requested_version_date} "
                f"before={before} on_or_after={after} "
                f"truth_claim={change_window.truth_claim}"
            )
        for version in versions[: args.limit]:
            print(f"{version.version_id}\t{version.version_date}\t{version.xml_locator}")
        if len(versions) > args.limit:
            print(f"... {len(versions) - args.limit} more")
        return

    diff = diff_archived_versions(
        db_path=Path(args.db),
        work_id=args.work_id,
        before_version_id=args.before_version_id or "",
        after_version_id=args.after_version_id or "",
    )
    if args.json:
        print(json.dumps(diff.to_jsonable(), ensure_ascii=False, indent=2))
        return
    summary = diff.summary()
    print(
        f"before={summary['before_version_id']} after={summary['after_version_id']} "
        f"changes={summary['changes']} change_counts={summary['change_counts']}"
    )
    for change in diff.changes[: args.limit]:
        print(
            f"{change.change_type}\t{'/'.join(change.path)}\t"
            f"{change.before_heading or '-'} -> {change.after_heading or '-'}"
        )
    if len(diff.changes) > args.limit:
        print(f"... {len(diff.changes) - args.limit} more")
