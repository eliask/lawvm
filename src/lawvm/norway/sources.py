"""Shared Norway source-store helpers.

Norway is transitioning from raw Lovdata tarballs as a direct runtime
dependency to the same Farchive-backed source boundary used elsewhere in
LawVM. The helpers here make that migration boring:

- resolve the effective Norway source path
- read current/original/amendment bytes by logical id
- iterate logical artifacts independent of whether backing storage is a legacy
  tar directory or a ``.farchive`` DB
- hydrate a Norway Farchive from the four public Lovdata tarballs
"""
from __future__ import annotations

import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from lxml import etree

from lawvm.norway.grafter import lovdata_amendment_filename_to_id, lovdata_filename_to_id

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NORWAY_DIR = _REPO_ROOT / "data" / "norway"
DEFAULT_NORWAY_DB = _REPO_ROOT / "data" / "norway.farchive"
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ARCHIVE_SPAN_RE = re.compile(r"^lovtidend-avd1-(\d{4})(?:-(\d{4}))?\.tar\.bz2$")
_NO_CURRENT_LOCATOR_RE = re.compile(r"^no://lov/(?P<date>\d{4}-\d{2}-\d{2}-\d+)/current\.xml$")
_NO_ORIGINAL_LOCATOR_RE = re.compile(r"^no://lov/(?P<date>\d{4}-\d{2}-\d{2}-\d+)/original\.lti\.xml$")
_NO_AMENDMENT_LOCATOR_RE = re.compile(r"^no://lovtid/(?P<date>\d{4}-\d{2}-\d{2}-\d+)/amendment\.xml$")


@dataclass(frozen=True)
class NOLocatedArtifact:
    locator: str
    logical_id: str
    source_name: str
    member_name: str
    payload: bytes


@dataclass(frozen=True)
class NOEffectiveDate:
    status: str
    effective_date: Optional[str] = None
    raw_text: str = ""


def resolve_no_source_path(path: Path | None = None) -> Path:
    """Return the effective Norway source path.

    Priority:
    1. explicit path argument
    2. ``LAWVM_NORWAY_DB``
    3. ``LAWVM_NORWAY_DATA_DIR``
    4. ``data/norway.farchive`` if present
    5. legacy ``data/norway`` directory
    """
    if path is not None:
        return path
    env_db = os.environ.get("LAWVM_NORWAY_DB")
    if env_db:
        return Path(env_db)
    env_dir = os.environ.get("LAWVM_NORWAY_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if DEFAULT_NORWAY_DB.exists():
        return DEFAULT_NORWAY_DB
    return DEFAULT_NORWAY_DIR


def is_no_farchive_path(path: Path | str) -> bool:
    path = Path(path)
    return path.suffix == ".farchive" or (path.exists() and path.is_file() and path.name.endswith(".farchive"))


def open_no_archive(db_path: Path | None = None, *, readonly: bool = False):  # returns Farchive
    from farchive import Farchive

    path = resolve_no_source_path(db_path)
    if not is_no_farchive_path(path):
        raise ValueError(f"Norway source path is not an farchive DB: {path}")
    return Farchive(path, readonly=readonly)


def no_current_locator(base_id: str) -> str:
    return f"no://lov/{base_id.removeprefix('no/lov/')}/current.xml"


def no_original_locator(base_id: str) -> str:
    return f"no://lov/{base_id.removeprefix('no/lov/')}/original.lti.xml"


def no_amendment_locator(source_id: str) -> str:
    return f"no://lovtid/{source_id.removeprefix('no/lovtid/')}/amendment.xml"


def no_base_id_from_current_locator(locator: str) -> str | None:
    match = _NO_CURRENT_LOCATOR_RE.fullmatch(locator.strip())
    if not match:
        return None
    return f"no/lov/{match.group('date')}"


def no_base_id_from_original_locator(locator: str) -> str | None:
    match = _NO_ORIGINAL_LOCATOR_RE.fullmatch(locator.strip())
    if not match:
        return None
    return f"no/lov/{match.group('date')}"


def no_source_id_from_amendment_locator(locator: str) -> str | None:
    match = _NO_AMENDMENT_LOCATOR_RE.fullmatch(locator.strip())
    if not match:
        return None
    return f"no/lovtid/{match.group('date')}"


def repair_mojibake(text: str) -> str:
    """Best-effort repair for common UTF-8-as-Latin-1 mojibake in Lovdata metadata."""
    if not text or not any(marker in text for marker in ("Ã", "Â", "â")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if repaired == text:
        return text
    original_markers = sum(text.count(marker) for marker in ("Ã", "Â", "â"))
    repaired_markers = sum(repaired.count(marker) for marker in ("Ã", "Â", "â"))
    if repaired_markers > original_markers:
        return text
    return repaired


def parse_header_value(html_bytes: bytes, dd_class: str) -> str:
    root = None
    xml_parser = etree.XMLParser(recover=True)
    try:
        root = etree.fromstring(html_bytes, parser=xml_parser)
    except etree.XMLSyntaxError:
        root = None
    if root is None:
        parser = etree.HTMLParser(recover=True)
        root = etree.fromstring(html_bytes, parser=parser)
    values = root.xpath(
        f"string(//dd[contains(concat(' ', normalize-space(@class), ' '), ' {dd_class} ')][1])"
    )
    normalized = " ".join(str(values).replace("\xa0", " ").split()).strip()
    return repair_mojibake(normalized)


def effective_date_from_amendment(html_bytes: bytes, source_date: str = "") -> NOEffectiveDate:
    raw = parse_header_value(html_bytes, "dateInForce")
    dates = ISO_DATE_RE.findall(raw)
    if not dates:
        lowered = raw.lower()
        if not raw:
            return NOEffectiveDate(status="missing", raw_text="")
        if "straks" in lowered and source_date:
            return NOEffectiveDate(status="immediate", effective_date=source_date, raw_text=raw)
        contingent_markers = (
            "kongen bestemmer",
            "kongen fastset",
            "departementet bestemmer",
            "fastsettes ved lov",
            "fra den tid",
        )
        if any(marker in lowered for marker in contingent_markers):
            return NOEffectiveDate(status="contingent", raw_text=raw)
        return NOEffectiveDate(status="unknown", raw_text=raw)
    return NOEffectiveDate(status="dated", effective_date=min(dates), raw_text=raw)


def archive_year_span(archive_path: Path) -> Optional[tuple[int, int]]:
    match = ARCHIVE_SPAN_RE.match(archive_path.name)
    if not match:
        return None
    start_year = int(match.group(1))
    end_year = int(match.group(2) or match.group(1))
    return start_year, end_year


def iter_lovtidend_archives(data_dir: Path) -> list[Path]:
    archives = []
    for path in data_dir.glob("lovtidend-avd1-*.tar.bz2"):
        span = archive_year_span(path)
        if span is None:
            continue
        archives.append((span, path))
    archives.sort(key=lambda item: (item[0][0], item[0][1], item[1].name))
    return [path for _span, path in archives]


def _iter_current_artifacts_from_dir(data_dir: Path) -> Iterator[NOLocatedArtifact]:
    current_archive = data_dir / "gjeldende-lover.tar.bz2"
    if not current_archive.exists():
        return
    with tarfile.open(current_archive, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            base_id = lovdata_filename_to_id(member.name)
            if base_id is None:
                continue
            file_obj = tf.extractfile(member)
            if file_obj is None:
                continue
            yield NOLocatedArtifact(
                locator=no_current_locator(base_id),
                logical_id=base_id,
                source_name=current_archive.name,
                member_name=member.name,
                payload=file_obj.read(),
            )


def _iter_lovtidend_members_from_dir(
    data_dir: Path,
) -> Iterator[tuple[str | None, str | None, str, str, bytes]]:
    for archive_path in iter_lovtidend_archives(data_dir):
        with tarfile.open(archive_path, "r:bz2") as tf:
            for member in tf.getmembers():
                if not member.name.endswith(".xml"):
                    continue
                file_obj = tf.extractfile(member)
                if file_obj is None:
                    continue
                payload = file_obj.read()
                yield (
                    lovdata_filename_to_id(member.name),
                    lovdata_amendment_filename_to_id(member.name),
                    archive_path.name,
                    member.name,
                    payload,
                )


def _iter_original_lti_artifacts_from_dir(data_dir: Path) -> Iterator[NOLocatedArtifact]:
    for base_id, _source_id, archive_name, member_name, payload in _iter_lovtidend_members_from_dir(data_dir):
        if base_id is None:
            continue
        yield NOLocatedArtifact(
            locator=no_original_locator(base_id),
            logical_id=base_id,
            source_name=archive_name,
            member_name=member_name,
            payload=payload,
        )


def _iter_amendment_artifacts_from_dir(data_dir: Path) -> Iterator[NOLocatedArtifact]:
    for _base_id, source_id, archive_name, member_name, payload in _iter_lovtidend_members_from_dir(data_dir):
        if source_id is None:
            continue
        yield NOLocatedArtifact(
            locator=no_amendment_locator(source_id),
            logical_id=source_id,
            source_name=archive_name,
            member_name=member_name,
            payload=payload,
        )


def _iter_artifacts_from_farchive(
    db_path: Path,
    *,
    pattern: str,
    id_from_locator: Any,
) -> Iterator[NOLocatedArtifact]:
    archive = open_no_archive(db_path, readonly=True)
    try:
        for locator in archive.locators(pattern):
            logical_id = id_from_locator(locator)
            if logical_id is None:
                continue
            payload = archive.get(locator)
            if payload is None:
                continue
            yield NOLocatedArtifact(
                locator=locator,
                logical_id=logical_id,
                source_name=db_path.name,
                member_name=locator,
                payload=payload,
            )
    finally:
        archive.close()


def iter_no_current_artifacts(source_path: Path | None = None) -> Iterator[NOLocatedArtifact]:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        yield from _iter_artifacts_from_farchive(
            source_path,
            pattern="no://lov/%/current.xml",
            id_from_locator=no_base_id_from_current_locator,
        )
        return
    yield from _iter_current_artifacts_from_dir(source_path)


def iter_no_original_lti_artifacts(source_path: Path | None = None) -> Iterator[NOLocatedArtifact]:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        yield from _iter_artifacts_from_farchive(
            source_path,
            pattern="no://lov/%/original.lti.xml",
            id_from_locator=no_base_id_from_original_locator,
        )
        return
    yield from _iter_original_lti_artifacts_from_dir(source_path)


def iter_no_amendment_artifacts(source_path: Path | None = None) -> Iterator[NOLocatedArtifact]:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        yield from _iter_artifacts_from_farchive(
            source_path,
            pattern="no://lovtid/%/amendment.xml",
            id_from_locator=no_source_id_from_amendment_locator,
        )
        return
    yield from _iter_amendment_artifacts_from_dir(source_path)


def load_no_current_bytes(base_id: str, source_path: Path | None = None) -> bytes | None:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        archive = open_no_archive(source_path, readonly=True)
        try:
            return archive.get(no_current_locator(base_id))
        finally:
            archive.close()
    for artifact in _iter_current_artifacts_from_dir(source_path):
        if artifact.logical_id == base_id:
            return artifact.payload
    return None


def load_no_original_lti_bytes(base_id: str, source_path: Path | None = None) -> bytes | None:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        archive = open_no_archive(source_path, readonly=True)
        try:
            return archive.get(no_original_locator(base_id))
        finally:
            archive.close()
    for artifact in _iter_original_lti_artifacts_from_dir(source_path):
        if artifact.logical_id == base_id:
            return artifact.payload
    return None


def load_no_amendment_bytes(source_id: str, source_path: Path | None = None) -> bytes | None:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        archive = open_no_archive(source_path, readonly=True)
        try:
            return archive.get(no_amendment_locator(source_id))
        finally:
            archive.close()
    for artifact in _iter_amendment_artifacts_from_dir(source_path):
        if artifact.logical_id == source_id:
            return artifact.payload
    return None


def load_no_current_law_ids(source_path: Path | None = None) -> set[str]:
    from lawvm.norway.grafter import parse_no_statute

    def _has_operative_content(node: Any) -> bool:
        if getattr(node, "kind", "") in {"section", "subsection", "item", "sentence"}:
            if getattr(node, "text", "") or getattr(node, "children", []):
                return True
        return any(_has_operative_content(child) for child in getattr(node, "children", []))

    def _payload_has_operative_content(payload: bytes) -> bool:
        text = payload.decode("utf-8", errors="ignore")
        if "legalArticleHeader" not in text:
            return False
        return any(marker in text for marker in ("legalP", "legalArticleText", "<p>", "<P>"))

    current_ids: set[str] = set()
    for artifact in iter_no_current_artifacts(source_path):
        try:
            statute = parse_no_statute(artifact.payload, artifact.logical_id)
        except Exception:
            if _payload_has_operative_content(artifact.payload):
                current_ids.add(artifact.logical_id)
            continue
        if _has_operative_content(statute.body) or _payload_has_operative_content(artifact.payload):
            current_ids.add(artifact.logical_id)
    return current_ids


def load_available_lti_law_ids(source_path: Path | None = None) -> set[str]:
    """Return canonical ``no/lov/...`` ids whose original LTI artifact exists locally."""
    return {artifact.logical_id for artifact in iter_no_original_lti_artifacts(source_path)}


def load_no_current_law_titles(source_path: Path | None = None) -> dict[str, str]:
    from lawvm.norway.grafter import parse_no_statute

    titles: dict[str, str] = {}
    for artifact in iter_no_current_artifacts(source_path):
        try:
            titles[artifact.logical_id] = parse_no_statute(artifact.payload, artifact.logical_id).title
        except Exception:
            continue
    return titles


def no_source_metadata(source_path: Path | None = None) -> dict[str, Any]:
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        if not source_path.exists():
            return {"source_kind": "farchive", "path": str(source_path), "exists": False}
        stat = source_path.stat()
        return {
            "source_kind": "farchive",
            "path": str(source_path),
            "exists": True,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    current_archive = source_path / "gjeldende-lover.tar.bz2"
    archive_paths = ([current_archive] if current_archive.exists() else []) + iter_lovtidend_archives(source_path)
    archive_metadata = {
        path.name: {"size": int(path.stat().st_size), "mtime_ns": int(path.stat().st_mtime_ns)}
        for path in archive_paths
    }
    return {
        "source_kind": "dir",
        "path": str(source_path),
        "exists": source_path.exists(),
        "archive_names": [path.name for path in archive_paths],
        "archive_metadata": archive_metadata,
    }


def ingest_no_public_archives(
    source_dir: Path,
    db_path: Path | None = None,
    *,
    skip_existing: bool = False,
) -> dict[str, int | str]:
    """Hydrate a Norway Farchive from local public Lovdata tarballs."""
    db_path = db_path or DEFAULT_NORWAY_DB
    archive = open_no_archive(db_path)
    report = {
        "source_dir": str(source_dir),
        "db_path": str(db_path),
        "current_locators_stored": 0,
        "original_locators_stored": 0,
        "amendment_locators_stored": 0,
        "skipped_existing": 0,
    }
    try:
        for artifact in _iter_current_artifacts_from_dir(source_dir):
            if skip_existing and archive.has(artifact.locator):
                report["skipped_existing"] += 1
                continue
            archive.store(
                artifact.locator,
                artifact.payload,
                storage_class="xml",
                metadata={"source_name": artifact.source_name, "member_name": artifact.member_name, "kind": "current"},
            )
            report["current_locators_stored"] += 1
        for artifact in _iter_original_lti_artifacts_from_dir(source_dir):
            if skip_existing and archive.has(artifact.locator):
                report["skipped_existing"] += 1
                continue
            archive.store(
                artifact.locator,
                artifact.payload,
                storage_class="xml",
                metadata={"source_name": artifact.source_name, "member_name": artifact.member_name, "kind": "original"},
            )
            report["original_locators_stored"] += 1
        for artifact in _iter_amendment_artifacts_from_dir(source_dir):
            if skip_existing and archive.has(artifact.locator):
                report["skipped_existing"] += 1
                continue
            archive.store(
                artifact.locator,
                artifact.payload,
                storage_class="xml",
                metadata={"source_name": artifact.source_name, "member_name": artifact.member_name, "kind": "amendment"},
            )
            report["amendment_locators_stored"] += 1
    finally:
        archive.close()
    return report
