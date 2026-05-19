"""Dependency extraction for New Zealand consolidated XML.

NZ API v0 does not expose an explicit amendment/effect graph. The best current
source surface for "which Acts are included in this consolidation" is the
consolidated XML reprint notes, especially ``reprint.amend`` entries.
Provision-level ``history-note`` entries then provide finer operation witnesses.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, cast

from lxml import etree

from lawvm.new_zealand.acquisition import open_farchive


class ArchiveReader(Protocol):
    def get(self, locator: str, *, at: object | None = None) -> bytes | None: ...
    def locators(self, pattern: str = "%") -> list[str]: ...
    def close(self) -> None: ...


_ACT_CITATION_RE = re.compile(
    r"(?:^|(?:of|under|pursuant to)\s+(?:the\s+)?)"
    r"(?P<title>[A-Z][^:.;]*?)\s+(?P<title_year>\d{4})\s+"
    r"\((?P<number_year>\d{4})\s+No\s+(?P<number>[0-9A-Za-z]+)\)"
)


@dataclass(frozen=True)
class NZAmendingWorkRef:
    work_id: str
    title: str
    year: str
    number: str
    source: str
    citation_text: str
    occurrence_count: int = 1

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "year": self.year,
            "number": self.number,
            "source": self.source,
            "citation_text": self.citation_text,
            "occurrence_count": self.occurrence_count,
        }


@dataclass(frozen=True)
class NZDependencyReport:
    xml_locator: str
    work_id: str
    version_id: str
    reprint_amendment_count: int
    history_note_count: int
    amending_works: tuple[NZAmendingWorkRef, ...]
    diagnostics: tuple[dict[str, Any], ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "xml_locator": self.xml_locator,
            "work_id": self.work_id,
            "version_id": self.version_id,
            "reprint_amendment_count": self.reprint_amendment_count,
            "history_note_count": self.history_note_count,
            "amending_works": [ref.to_jsonable() for ref in self.amending_works],
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class NZLatestXMLLocatorSelection:
    work_id: str
    version_id: str
    xml_locator: str
    diagnostics: tuple[dict[str, Any], ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "version_id": self.version_id,
            "xml_locator": self.xml_locator,
            "diagnostics": list(self.diagnostics),
        }


def extract_dependency_report(
    *,
    xml_bytes: bytes,
    xml_locator: str,
    work_id: str = "",
    version_id: str = "",
) -> NZDependencyReport:
    root = etree.fromstring(xml_bytes)
    reprint_refs = list(_iter_reprint_amend_refs(root))
    history_refs = list(_iter_history_note_refs(root))
    diagnostics: list[dict[str, Any]] = []
    by_work: dict[str, NZAmendingWorkRef] = {}
    counts: Counter[str] = Counter()

    for ref in reprint_refs:
        counts[ref.work_id] += 1
        by_work.setdefault(ref.work_id, ref)
    for ref in history_refs:
        counts[ref.work_id] += 1
        if ref.work_id not in by_work:
            by_work[ref.work_id] = ref

    for text in _iter_reprint_amend_texts(root):
        if _parse_act_citation(text) is None:
            diagnostics.append(
                {
                    "rule_id": "nz_dependency_reprint_amend_unparsed",
                    "phase": "acquisition",
                    "family": "source_pathology",
                    "reason": "reprint.amend citation did not match public Act citation pattern",
                    "citation_text": text,
                }
            )

    refs = tuple(
        NZAmendingWorkRef(
            work_id=ref.work_id,
            title=ref.title,
            year=ref.year,
            number=ref.number,
            source=ref.source,
            citation_text=ref.citation_text,
            occurrence_count=counts[ref.work_id],
        )
        for ref in sorted(by_work.values(), key=_ref_sort_key, reverse=True)
    )
    return NZDependencyReport(
        xml_locator=xml_locator,
        work_id=work_id,
        version_id=version_id,
        reprint_amendment_count=len(reprint_refs),
        history_note_count=sum(1 for _ in _iter_localname(root, "history-note")),
        amending_works=refs,
        diagnostics=tuple(diagnostics),
    )


def latest_xml_locator_for_work(archive: ArchiveReader, work_id: str) -> tuple[str, str]:
    """Return ``(version_id, xml_locator)`` for the newest archived version detail."""
    selection = latest_xml_locator_selection_for_work(archive, work_id)
    return selection.version_id, selection.xml_locator


def latest_xml_locator_selection_for_work(archive: ArchiveReader, work_id: str) -> NZLatestXMLLocatorSelection:
    """Return the latest archived XML locator plus rejected source-lane diagnostics."""
    prefix = f"https://api.legislation.govt.nz/v0/versions/{work_id}_en_"
    version_locs = sorted(archive.locators(prefix + "%"), reverse=True)
    diagnostics: list[dict[str, Any]] = []
    for loc in version_locs:
        version_id = loc.rstrip("/").rsplit("/", 1)[-1]
        detail_bytes = archive.get(loc)
        if detail_bytes is None:
            diagnostics.append(
                _latest_xml_locator_candidate_diagnostic(
                    work_id=work_id,
                    version_id=version_id,
                    version_locator=loc,
                    reason_code="detail_missing",
                    reason="NZ version detail candidate was skipped because the archived detail JSON is missing",
                )
            )
            continue
        try:
            detail = json.loads(detail_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            diagnostics.append(
                _latest_xml_locator_candidate_diagnostic(
                    work_id=work_id,
                    version_id=version_id,
                    version_locator=loc,
                    reason_code="detail_json_invalid",
                    reason="NZ version detail candidate was skipped because the archived detail JSON is invalid",
                    detail={"json_error": str(exc)},
                )
            )
            continue
        xml_locator = _xml_locator_from_version_detail(detail)
        if not xml_locator:
            diagnostics.append(
                _latest_xml_locator_candidate_diagnostic(
                    work_id=work_id,
                    version_id=version_id,
                    version_locator=loc,
                    reason_code="xml_locator_missing",
                    reason="NZ version detail candidate was skipped because it exposes no XML format locator",
                )
            )
            continue
        if archive.get(xml_locator) is None:
            diagnostics.append(
                _latest_xml_locator_candidate_diagnostic(
                    work_id=work_id,
                    version_id=version_id,
                    version_locator=loc,
                    reason_code="xml_not_archived",
                    reason="NZ version detail candidate was skipped because its XML locator is not archived",
                    detail={"xml_locator": xml_locator},
                )
            )
            continue
        return NZLatestXMLLocatorSelection(
            work_id=work_id,
            version_id=version_id,
            xml_locator=xml_locator,
            diagnostics=tuple(diagnostics),
        )
    return NZLatestXMLLocatorSelection(work_id=work_id, version_id="", xml_locator="", diagnostics=tuple(diagnostics))


def _latest_xml_locator_candidate_diagnostic(
    *,
    work_id: str,
    version_id: str,
    version_locator: str,
    reason_code: str,
    reason: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": "nz_latest_xml_locator_candidate_rejected",
        "phase": "acquisition",
        "family": "source_pathology",
        "work_id": work_id,
        "version_id": version_id,
        "version_locator": version_locator,
        "reason": reason,
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "detail": {"reason_code": reason_code, **dict(detail or {})},
    }


def _xml_locator_from_version_detail(detail: Mapping[str, Any]) -> str:
    version_id = str(detail.get("version_id") or "")
    version_date = version_id.rsplit("_", 1)[-1] if "_" in version_id else ""
    formats = detail.get("formats")
    if not isinstance(formats, list):
        return ""
    for row in formats:
        if not isinstance(row, Mapping):
            continue
        row_map = cast(Mapping[str, Any], row)
        url = str(row_map.get("url") or "")
        kind = str(row_map.get("type") or row_map.get("format") or "").lower()
        if kind == "xml" or url.endswith(".xml"):
            if version_date:
                return url.replace("/latest.xml", f"/{version_date}.xml")
            return url
    return ""


def _iter_reprint_amend_refs(root: etree._Element) -> Iterable[NZAmendingWorkRef]:
    for text in _iter_reprint_amend_texts(root):
        parsed = _parse_act_citation(text)
        if parsed is None:
            continue
        title, year, number = parsed
        yield NZAmendingWorkRef(
            work_id=_public_act_work_id(year, number),
            title=title,
            year=year,
            number=number,
            source="reprint.amend",
            citation_text=text,
        )


def _iter_history_note_refs(root: etree._Element) -> Iterable[NZAmendingWorkRef]:
    for node in _iter_localname(root, "history-note"):
        text = _node_text(node)
        parsed = _parse_act_citation(text)
        if parsed is None:
            continue
        title, year, number = parsed
        yield NZAmendingWorkRef(
            work_id=_public_act_work_id(year, number),
            title=title,
            year=year,
            number=number,
            source="history-note",
            citation_text=text,
        )


def _iter_reprint_amend_texts(root: etree._Element) -> Iterable[str]:
    for node in _iter_localname(root, "reprint.amend"):
        text = _node_text(node)
        if text:
            yield text


def _iter_localname(root: etree._Element, localname: str) -> Iterable[etree._Element]:
    for node in root.iter():
        if isinstance(node.tag, str) and etree.QName(node).localname == localname:
            yield node


def _node_text(node: etree._Element) -> str:
    return " ".join("".join(str(part) for part in node.itertext()).split())


def _parse_act_citation(text: str) -> tuple[str, str, str] | None:
    return parse_public_act_citation(text)


def parse_public_act_citation(text: str) -> tuple[str, str, str] | None:
    """Parse a NZ public Act citation into ``(title, year, number)``."""
    matches = list(_ACT_CITATION_RE.finditer(text))
    match = matches[-1] if matches else None
    if match is None:
        return None
    title_year = match.group("title_year")
    number_year = match.group("number_year")
    if title_year != number_year:
        return None
    return (
        match.group("title").strip(),
        number_year,
        match.group("number").lstrip("0") or "0",
    )


def _public_act_work_id(year: str, number: str) -> str:
    return f"act_public_{year}_{number.lstrip('0') or '0'}"


def _ref_sort_key(ref: NZAmendingWorkRef) -> tuple[int, int, str, str]:
    numeric = int(ref.number) if ref.number.isdigit() else -1
    return int(ref.year), numeric, ref.number, ref.title


def main(args: Any) -> None:
    archive = open_farchive(Path(args.db))
    selector_diagnostics: tuple[dict[str, Any], ...] = ()
    try:
        xml_locator = args.xml_locator or ""
        version_id = args.version_id or ""
        if not xml_locator:
            if not args.work_id:
                raise SystemExit("ERROR: pass --work-id or --xml-locator")
            selection = latest_xml_locator_selection_for_work(archive, args.work_id)
            version_id = selection.version_id
            xml_locator = selection.xml_locator
            selector_diagnostics = selection.diagnostics
            if not xml_locator:
                raise SystemExit(f"ERROR: no archived latest XML found for {args.work_id}")
        data = archive.get(xml_locator)
    finally:
        archive.close()
    if data is None:
        raise SystemExit(f"ERROR: XML locator not archived: {xml_locator}")

    report = extract_dependency_report(
        xml_bytes=data,
        xml_locator=xml_locator,
        work_id=args.work_id or "",
        version_id=version_id,
    )
    if selector_diagnostics:
        report = NZDependencyReport(
            xml_locator=report.xml_locator,
            work_id=report.work_id,
            version_id=report.version_id,
            reprint_amendment_count=report.reprint_amendment_count,
            history_note_count=report.history_note_count,
            amending_works=report.amending_works,
            diagnostics=selector_diagnostics + report.diagnostics,
        )
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2))
        return
    print(
        f"work_id={report.work_id or '-'} version_id={report.version_id or '-'} "
        f"amending_works={len(report.amending_works)} "
        f"reprint_amendments={report.reprint_amendment_count} "
        f"history_notes={report.history_note_count} diagnostics={len(report.diagnostics)}"
    )
    for ref in report.amending_works[: args.limit]:
        print(f"{ref.work_id}\t{ref.year} No {ref.number}\t{ref.title}\toccurrences={ref.occurrence_count}")
    if len(report.amending_works) > args.limit:
        print(f"... {len(report.amending_works) - args.limit} more")
