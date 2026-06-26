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

import hashlib
import os
import re
import tarfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Optional, cast

from lxml import etree

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir_helpers import kind_str
from lawvm.core.source_lane import SourceLaneAttempt, SourceLaneSelectionEvidence
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


class NOEffectiveStatus(StrEnum):
    """Closed set of commencement (in-force) resolution outcomes for a NO act.

    A ``StrEnum`` so the value flows through the serialized ``effective_status``
    dict/field and test ``== "..."`` comparisons byte-for-byte while the value
    set is closed.
    """

    DATED = "dated"
    """A concrete in-force date was resolved."""

    IMMEDIATE = "immediate"
    """In force on the source/promulgation date."""

    OVERRIDE = "override"
    """An explicit commencement override supplied the in-force date."""

    CONTINGENT = "contingent"
    """In force on a condition / future delegated commencement (unresolved)."""

    MISSING = "missing"
    """No in-force signal present in the source."""

    UNKNOWN = "unknown"
    """An in-force signal was present but not interpretable."""


# Statuses that count as a RESOLVED in-force date (replayable). The complement
# (contingent/missing/unknown) blocks deterministic replay.
NO_RESOLVED_EFFECTIVE_STATUSES: frozenset[NOEffectiveStatus] = frozenset(
    {NOEffectiveStatus.DATED, NOEffectiveStatus.IMMEDIATE, NOEffectiveStatus.OVERRIDE}
)
NO_UNRESOLVED_EFFECTIVE_STATUSES: frozenset[NOEffectiveStatus] = frozenset(
    {NOEffectiveStatus.CONTINGENT, NOEffectiveStatus.MISSING, NOEffectiveStatus.UNKNOWN}
)


class NOReplayStatus(StrEnum):
    """Closed set of per-base-law replayability classifications.

    Derived from the in-force statuses of a base law's amendments. A ``StrEnum``
    so it flows through serialized status maps / test comparisons byte-for-byte.
    """

    NO_AMENDMENTS = "no_amendments"
    """The base law has no amendments to replay."""

    FULLY_REPLAYABLE = "fully_replayable"
    """Every amendment has a resolved in-force date."""

    BLOCKED_CONTINGENT = "blocked_contingent"
    """At least one amendment is contingent (future/conditional commencement)."""

    BLOCKED_UNKNOWN = "blocked_unknown"
    """At least one amendment has a missing/unknown in-force status."""


def no_base_replay_status_from_statuses(
    statuses: list[NOEffectiveStatus] | list[str],
) -> NOReplayStatus:
    """Classify a base law's replayability from its amendments' in-force statuses.

    Single source of truth for the rule shared by the inventory and the
    commencement report (was duplicated in both).
    """
    if not statuses:
        return NOReplayStatus.NO_AMENDMENTS
    if any(status == NOEffectiveStatus.CONTINGENT for status in statuses):
        return NOReplayStatus.BLOCKED_CONTINGENT
    if any(status not in NO_RESOLVED_EFFECTIVE_STATUSES for status in statuses):
        return NOReplayStatus.BLOCKED_UNKNOWN
    return NOReplayStatus.FULLY_REPLAYABLE


class NOBackfillLane(StrEnum):
    """Closed set of recommended source-acquisition lanes for a NO backfill.

    Derived from which candidate-source families surfaced. A ``StrEnum`` so it
    flows through serialized ``recommended_lane`` dict keys / advisory output and
    test comparisons byte-for-byte.
    """

    MIXED = "mixed"
    """Both local_corpus and statsrad produced candidates."""

    STATSRAD = "statsrad"
    """Only statsrad candidates surfaced."""

    LOCAL_CORPUS = "local_corpus"
    """Only local_corpus candidates surfaced."""

    UNRESOLVED = "unresolved"
    """No candidate surfaced in any lane."""


class NOBackfillHintStatus(StrEnum):
    """Closed set of next-source recommendation states for a NO backfill hint.

    Derived from the recommended backfill lane (``NOBackfillLane``). A
    ``StrEnum`` so it flows through the serialized ``hint_status`` dict key /
    advisory output and test comparisons byte-for-byte while the value set is
    closed.
    """

    NEEDS_EXTERNAL_OFFICIAL_SOURCE = "needs_external_official_source"
    """No local_corpus/statsrad candidate surfaced — search external channels."""

    COMPARE_EXISTING_LANES = "compare_existing_lanes"
    """Both local_corpus and statsrad produced candidates — compare them."""

    STATSRAD_FIRST = "statsrad_first"
    """Only statsrad candidates surfaced — start there."""

    LOCAL_CORPUS_FIRST = "local_corpus_first"
    """Only local_corpus candidates surfaced — start there."""


class NOBackfillPlanStatus(StrEnum):
    """Closed set of per-source-plan-item states for a NO backfill plan.

    A ``StrEnum`` so it flows through the serialized ``plan_status`` dict key /
    advisory output and test comparisons byte-for-byte while the value set is
    closed.
    """

    CANDIDATE = "candidate"
    """A surfaced candidate source family to search/compare."""

    NEXT_OFFICIAL_SOURCE = "next_official_source"
    """An external official publication channel to search next."""

    FALLBACK_HISTORY = "fallback_history"
    """A deeper historical layer to fall back to."""


@dataclass(frozen=True)
class NOEffectiveDate:
    effective_status: NOEffectiveStatus
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


def open_no_archive(db_path: Path | None = None, *, readonly: bool = True):  # returns Farchive
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
            return NOEffectiveDate(effective_status=NOEffectiveStatus.MISSING, raw_text="")
        if "straks" in lowered and source_date:
            return NOEffectiveDate(
                effective_status=NOEffectiveStatus.IMMEDIATE, effective_date=source_date, raw_text=raw
            )
        contingent_markers = (
            "kongen bestemmer",
            "kongen fastset",
            "departementet bestemmer",
            "fastsettes ved lov",
            "fra den tid",
        )
        if any(marker in lowered for marker in contingent_markers):
            return NOEffectiveDate(effective_status=NOEffectiveStatus.CONTINGENT, raw_text=raw)
        return NOEffectiveDate(effective_status=NOEffectiveStatus.UNKNOWN, raw_text=raw)
    return NOEffectiveDate(
        effective_status=NOEffectiveStatus.DATED, effective_date=min(dates), raw_text=raw
    )


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


def iter_no_unmapped_lovtidend_xml_members(source_path: Path | None = None) -> Iterator[NOLocatedArtifact]:
    """Yield Lovtidend XML members whose filename cannot be mapped to a legal source id."""
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        return
    for base_id, source_id, archive_name, member_name, payload in _iter_lovtidend_members_from_dir(source_path):
        if base_id is not None or source_id is not None:
            continue
        yield NOLocatedArtifact(
            locator="",
            logical_id="",
            source_name=archive_name,
            member_name=member_name,
            payload=payload,
        )


def iter_no_unmapped_current_xml_members(source_path: Path | None = None) -> Iterator[NOLocatedArtifact]:
    """Yield current-law XML members whose filename cannot be mapped to a law id."""
    source_path = resolve_no_source_path(source_path)
    if is_no_farchive_path(source_path):
        return
    current_archive = source_path / "gjeldende-lover.tar.bz2"
    if not current_archive.exists():
        return
    with tarfile.open(current_archive, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            if lovdata_filename_to_id(member.name) is not None:
                continue
            file_obj = tf.extractfile(member)
            if file_obj is None:
                continue
            yield NOLocatedArtifact(
                locator="",
                logical_id="",
                source_name=current_archive.name,
                member_name=member.name,
                payload=file_obj.read(),
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


def load_no_amendment_artifact_bytes(
    source_id: str,
    archive_name: str,
    member_name: str,
    source_path: Path | None = None,
) -> bytes | None:
    source_path = resolve_no_source_path(source_path)
    if not archive_name or not member_name:
        return load_no_amendment_bytes(source_id, source_path)
    if is_no_farchive_path(source_path):
        archive = open_no_archive(source_path, readonly=True)
        try:
            locator = member_name if member_name.startswith("no://") else no_amendment_locator(source_id)
            return archive.get(locator)
        finally:
            archive.close()
    archive_path = source_path / archive_name
    if not archive_path.exists():
        return None
    with tarfile.open(archive_path, "r:bz2") as tf:
        for member in tf.getmembers():
            if member.name != member_name:
                continue
            file_obj = tf.extractfile(member)
            if file_obj is None:
                return None
            return file_obj.read()
    return None


# §§ 1.9 / 1.10 — module-scope IR-operative-content predicate.
#
# The previous shape was a nested closure inside ``load_no_current_law_ids``
# with this membership test:
#
#     if getattr(node, "kind", "") in {"section", "subsection", "item", "sentence"}:
#
# which silently returned False on every IRNode whose ``kind`` is an
# ``IRNodeKind`` enum member (enum members don't equal their string values).
# The OR-clause's right side (``_payload_has_operative_content``) was the
# sole authority — the IR walk was dead code, an invisible §1.10 heuristic
# that lied about whether the IR carried operative content.
#
# The fix uses ``kind_str`` coercion (the same pattern as
# ``_no_kind_value`` in verify.py:269); both enum and plain-str kinds now
# participate. Hoisted to module scope so the predicate is unit-testable
# directly against synthetic IRNodes.
_NO_OPERATIVE_KINDS: frozenset[str] = frozenset(
    {"section", "subsection", "item", "sentence"}
)


def _has_operative_content(node: Any) -> bool:
    """Return True if *node* (or any descendant) carries operative section content.

    A node is operative when its ``kind`` is one of the leaf-bearing legal-unit
    kinds (section / subsection / item / sentence) AND it has either populated
    text or non-empty children. The IR-walk recurses into any non-leaf node
    (the body, chapter, …) so the recursive case remains the authority when
    the inspected level is a container, not a leaf.

    Honors both IRNode with ``IRNodeKind`` enum kind and any legacy str-typed
    kind — coercion goes through :func:`lawvm.core.ir_helpers.kind_str`, the
    shared canonical-string projection (mirroring ``_no_kind_value`` in
    verify.py).
    """
    kind_value = kind_str(getattr(node, "kind", ""))
    if kind_value in _NO_OPERATIVE_KINDS:
        if getattr(node, "text", "") or getattr(node, "children", []):
            return True
    return any(_has_operative_content(child) for child in getattr(node, "children", []))


def load_no_current_law_ids(
    source_path: Path | None = None,
    *,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> set[str]:
    from lawvm.norway.grafter import parse_no_statute

    def _payload_has_operative_content(payload: bytes) -> bool:
        text = payload.decode("utf-8", errors="ignore")
        if "legalArticleHeader" not in text:
            return False
        return any(marker in text for marker in ("legalP", "legalArticleText", "<p>", "<P>"))

    current_ids: set[str] = set()
    for artifact in iter_no_current_artifacts(source_path):
        try:
            statute = parse_no_statute(artifact.payload, artifact.logical_id)
        except Exception as exc:
            has_marker_fallback = _payload_has_operative_content(artifact.payload)
            if diagnostics_out is not None:
                rule_id = (
                    "no_current_law_id_parse_marker_fallback_used"
                    if has_marker_fallback
                    else "no_current_law_id_parse_skipped"
                )
                diagnostics_out.append(
                    diagnostic_detail(
                        rule_id=rule_id,
                        phase="parse",
                        family="source_pathology",
                        reason=(
                            "Norway current-law ID loader retained an artifact via operative marker fallback "
                            "after statute parsing failed."
                            if has_marker_fallback
                            else "Norway current-law ID loader skipped an artifact because statute parsing failed."
                        ),
                        blocking=True,
                        strict_disposition="block",
                        quirks_disposition="record",
                        statute_id=artifact.logical_id,
                        locator=artifact.locator,
                        source_name=artifact.source_name,
                        member_name=artifact.member_name,
                        exception_type=type(exc).__name__,
                        error=str(exc),
                        retained_by_marker_fallback=has_marker_fallback,
                    )
                )
            if has_marker_fallback:
                current_ids.add(artifact.logical_id)
            continue
        if _has_operative_content(statute.body) or _payload_has_operative_content(artifact.payload):
            current_ids.add(artifact.logical_id)
    return current_ids


def load_available_lti_law_ids(source_path: Path | None = None) -> set[str]:
    """Return canonical ``no/lov/...`` ids whose original LTI artifact exists locally."""
    return {artifact.logical_id for artifact in iter_no_original_lti_artifacts(source_path)}


def load_no_current_law_titles(
    source_path: Path | None = None,
    *,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    from lawvm.norway.grafter import parse_no_statute

    titles: dict[str, str] = {}
    for artifact in iter_no_current_artifacts(source_path):
        try:
            titles[artifact.logical_id] = parse_no_statute(artifact.payload, artifact.logical_id).title
        except Exception as exc:
            if diagnostics_out is not None:
                diagnostics_out.append(
                    diagnostic_detail(
                        rule_id="no_current_law_title_parse_skipped",
                        phase="parse",
                        family="source_pathology",
                        reason="Norway current-law title extraction skipped an artifact because statute parsing failed.",
                        blocking=True,
                        strict_disposition="block",
                        quirks_disposition="record",
                        statute_id=artifact.logical_id,
                        locator=artifact.locator,
                        source_name=artifact.source_name,
                        member_name=artifact.member_name,
                        exception_type=type(exc).__name__,
                        error=str(exc),
                    )
                )
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


def _no_archive_member_locator(artifact: NOLocatedArtifact) -> str:
    return f"{artifact.source_name}:{artifact.member_name}"


def _no_ingest_duplicate_locator_source_lane_evidence(
    *,
    artifact: NOLocatedArtifact,
    identical_payloads: bool,
    existing_payload: bytes,
) -> dict[str, Any]:
    return SourceLaneSelectionEvidence(
        rule_id="no_acquisition_duplicate_logical_locator",
        phase="acquisition",
        reason=(
            "Norway Farchive ingest retained the existing byte-identical source witness."
            if identical_payloads
            else "Norway Farchive ingest found a conflicting duplicate source witness and retained the existing lane."
        ),
        selected_lane="existing_farchive_locator",
        selected_locator=artifact.locator,
        attempts=(
            SourceLaneAttempt(
                lane="existing_farchive_locator",
                locator=artifact.locator,
                lane_attempt_status="selected_existing_identical" if identical_payloads else "selected_existing_conflict",
                detail={
                    "logical_id": artifact.logical_id,
                    "payload_digest": hashlib.sha256(existing_payload).hexdigest(),
                },
            ),
            SourceLaneAttempt(
                lane="incoming_archive_member",
                locator=_no_archive_member_locator(artifact),
                lane_attempt_status="duplicate_identical_not_stored" if identical_payloads else "blocked_conflicting_duplicate",
                detail={
                    "logical_id": artifact.logical_id,
                    "logical_locator": artifact.locator,
                    "payload_digest": hashlib.sha256(artifact.payload).hexdigest(),
                },
            ),
        ),
        blocking=True,
        strict_disposition="block",
        quirks_disposition="select_existing_identical" if identical_payloads else "block",
        detail={
            "logical_id": artifact.logical_id,
            "logical_locator": artifact.locator,
            "identical_payloads": identical_payloads,
        },
    ).to_diagnostic_detail()


def ingest_no_public_archives(
    source_dir: Path,
    db_path: Path | None = None,
    *,
    skip_existing: bool = False,
) -> dict[str, object]:
    """Hydrate a Norway Farchive from local public Lovdata tarballs."""
    db_path = db_path or DEFAULT_NORWAY_DB
    archive = open_no_archive(db_path, readonly=False)
    skipped_existing_entries: list[dict[str, str]] = []
    skipped_unmapped_entries: list[dict[str, Any]] = []
    duplicate_locator_entries: list[dict[str, Any]] = []
    report: dict[str, object] = {
        "source_dir": str(source_dir),
        "db_path": str(db_path),
        "current_locators_stored": 0,
        "original_locators_stored": 0,
        "amendment_locators_stored": 0,
        "skipped_existing": 0,
        "skipped_existing_entries": skipped_existing_entries,
        "skipped_unmapped": 0,
        "skipped_unmapped_entries": skipped_unmapped_entries,
        "duplicate_locator_count": 0,
        "duplicate_locator_entries": duplicate_locator_entries,
    }

    def _record_skipped_existing(artifact: NOLocatedArtifact, *, kind: str) -> None:
        report["skipped_existing"] = cast(int, report["skipped_existing"]) + 1
        skipped_existing_entries.append(
            {
                "rule_id": "no_ingest_existing_locator_skipped",
                "phase": "acquisition",
                "family": "transport_cleanup",
                "reason": "archive already contains locator and skip_existing was enabled",
                "kind": kind,
                "locator": artifact.locator,
                "logical_id": artifact.logical_id,
                "source_name": artifact.source_name,
                "member_name": artifact.member_name,
            }
        )

    def _record_skipped_unmapped(artifact: NOLocatedArtifact, *, kind: str) -> None:
        report["skipped_unmapped"] = cast(int, report["skipped_unmapped"]) + 1
        skipped_unmapped_entries.append(
            diagnostic_detail(
                rule_id="no_ingest_unmapped_xml_member",
                phase="acquisition",
                family="source_pathology",
                reason="Norway Lovdata XML member filename could not be mapped to a legal source id",
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
                kind=kind,
                locator=artifact.locator,
                logical_id=artifact.logical_id,
                source_name=artifact.source_name,
                member_name=artifact.member_name,
            )
        )

    def _record_duplicate_locator(
        artifact: NOLocatedArtifact,
        *,
        kind: str,
        existing_payload: bytes,
    ) -> None:
        identical_payloads = existing_payload == artifact.payload
        report["duplicate_locator_count"] = cast(int, report["duplicate_locator_count"]) + 1
        duplicate_locator_entries.append(
            diagnostic_detail(
                rule_id="no_acquisition_duplicate_logical_locator",
                phase="acquisition",
                family="source_pathology",
                reason=(
                    "Norway Farchive ingest found a byte-identical duplicate logical source locator; "
                    "the existing witness was retained."
                    if identical_payloads
                    else "Norway Farchive ingest found a conflicting duplicate logical source locator; "
                    "the existing witness was retained and the new payload was not stored."
                ),
                blocking=True,
                strict_disposition="block",
                quirks_disposition="select_existing_identical" if identical_payloads else "block",
                kind=kind,
                locator=artifact.locator,
                logical_id=artifact.logical_id,
                source_name=artifact.source_name,
                member_name=artifact.member_name,
                existing_payload_digest=hashlib.sha256(existing_payload).hexdigest(),
                new_payload_digest=hashlib.sha256(artifact.payload).hexdigest(),
                identical_payloads=identical_payloads,
                source_lane_selection=_no_ingest_duplicate_locator_source_lane_evidence(
                    artifact=artifact,
                    identical_payloads=identical_payloads,
                    existing_payload=existing_payload,
                ),
            )
        )

    try:
        for artifact in iter_no_unmapped_current_xml_members(source_dir):
            _record_skipped_unmapped(artifact, kind="current")
        for artifact in iter_no_unmapped_lovtidend_xml_members(source_dir):
            _record_skipped_unmapped(artifact, kind="lovtidend")
        for artifact in _iter_current_artifacts_from_dir(source_dir):
            if skip_existing and archive.has(artifact.locator):
                _record_skipped_existing(artifact, kind="current")
                continue
            if archive.has(artifact.locator):
                _record_duplicate_locator(
                    artifact,
                    kind="current",
                    existing_payload=archive.get(artifact.locator) or b"",
                )
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
                _record_skipped_existing(artifact, kind="original")
                continue
            if archive.has(artifact.locator):
                _record_duplicate_locator(
                    artifact,
                    kind="original",
                    existing_payload=archive.get(artifact.locator) or b"",
                )
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
                _record_skipped_existing(artifact, kind="amendment")
                continue
            if archive.has(artifact.locator):
                _record_duplicate_locator(
                    artifact,
                    kind="amendment",
                    existing_payload=archive.get(artifact.locator) or b"",
                )
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
