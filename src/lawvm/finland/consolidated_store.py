"""Finland-owned access layer for cached consolidated-oracle artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import logging
from typing import Protocol

log = logging.getLogger(__name__)
_SEEN_COLLAPSED_DATE_WARNINGS: set[tuple[str, str, dt.date | None, dt.date]] = set()

from lxml import etree

from lawvm.finland.consolidated_artifacts import (
    artifact_record,
    canonical_consolidated_locator,
    ConsolidatedArtifactSelector,
    ConsolidatedSelectionMode,
    ConsolidatedArtifactRecord,
    build_versioned_consolidated_main_glob,
    select_consolidated_record,
)


class ConsolidatedArchiveLike(Protocol):
    def get(self, url: str) -> bytes | None: ...
    def locators(self, pattern: str = "%") -> list[str]: ...


@dataclass(frozen=True)
class CachedConsolidatedArtifact:
    sid: str
    locator: str
    canonical_locator: str
    xml: bytes
    version_tag: str
    date_consolidated: dt.date | None


def _cached_artifact_record(
    artifact: CachedConsolidatedArtifact,
    *,
    lang: str,
) -> ConsolidatedArtifactRecord:
    """Project a cached artifact into the shared selector record shape."""
    return ConsolidatedArtifactRecord(
        locator=artifact.locator,
        namespace="sd-cons",
        sid=artifact.sid,
        lang=lang,
        path_version=artifact.version_tag,
        embedded_version_tag=artifact.version_tag,
        date_consolidated=artifact.date_consolidated,
    )


@dataclass(frozen=True)
class SelectionProvenance:
    """Provenance record for a ``select_cached_consolidated_artifact`` call.

    Carries enough information to populate ``OracleSelectorInfo`` on
    ``ReplayResult`` without requiring callers to re-derive the decision.

    Fields
    ------
    selector_mode:
        The ``ConsolidatedSelectionMode`` value (as a string) used for the
        final selection.  For BENCH_COMPARABLE calls this is always
        ``"bench_comparable"``; the function internally falls back to
        ``latest_cached_editorial`` after filtering — but the *caller's*
        intent is ``bench_comparable`` and that is what we record.
    chosen_version_tag:
        The embedded version tag of the selected artifact, or ``""`` if
        nothing was selected.
    tolerance_applied:
        True when at least one artifact was accepted under the 180-day
        Finlex-ahead tolerance (Option Z).  False when every candidate
        either had ordering_date <= date_consolidated or no date_consolidated.
    rejected_version_tags:
        Version tags of artifacts that were screened out by the
        BENCH_COMPARABLE comparability filter.  Empty tuple for non-
        BENCH_COMPARABLE calls or when all artifacts passed.
    """

    selector_mode: str = ""
    chosen_version_tag: str = ""
    tolerance_applied: bool = False
    rejected_version_tags: tuple[str, ...] = field(default_factory=tuple)


def _is_self_comparable_with_tolerance(
    artifact: CachedConsolidatedArtifact,
    archive: ConsolidatedArchiveLike,
) -> tuple[bool, bool]:
    """Return ``(is_comparable, tolerance_applied)`` for bench-comparable check.

    ``tolerance_applied`` is True when the artifact was accepted under the
    180-day Finlex-ahead tolerance (Option Z), i.e. ordering_date is in
    ``(date_consolidated, date_consolidated + 180 days]``.
    """
    amendment_id = _version_tag_to_amendment_id(artifact.version_tag)
    if not amendment_id:
        return False, False

    from lawvm.corpus_store import statute_url
    from lawvm.finland.metadata import (
        _amendment_effective_date,
        _amendment_expiry_date,
        _statute_issue_date,
    )

    source_bytes = archive.get(statute_url(amendment_id))
    if source_bytes is None:
        return False, False
    try:
        tree = etree.fromstring(source_bytes)
    except etree.XMLSyntaxError:
        return False, False

    effective_date = _amendment_effective_date(tree)
    issue_date = _statute_issue_date(tree)
    expiry_date = _amendment_expiry_date(tree)
    ordering_date = effective_date or issue_date

    if ordering_date is None:
        return False, False

    tolerance_applied = False
    if artifact.date_consolidated is not None:
        gap_days = (ordering_date - artifact.date_consolidated).days
        if gap_days > 180:
            return False, False
        if gap_days > 0:
            tolerance_applied = True
            warning_key = (
                artifact.sid,
                artifact.version_tag,
                artifact.date_consolidated,
                ordering_date,
            )
            if warning_key not in _SEEN_COLLAPSED_DATE_WARNINGS:
                _SEEN_COLLAPSED_DATE_WARNINGS.add(warning_key)
                log.info(
                    "ORACLE_METADATA_COLLAPSED_DATES sid=%s version_tag=%s "
                    "date_consolidated=%s ordering_date=%s gap_days=%d "
                    "— accepting artifact under Option Z (within 180-day tolerance)",
                    artifact.sid,
                    artifact.version_tag,
                    artifact.date_consolidated,
                    ordering_date,
                    gap_days,
                )

    if expiry_date is not None and expiry_date <= ordering_date:
        return False, False

    return True, tolerance_applied


def _select_from_cached_artifacts(
    artifacts: list[CachedConsolidatedArtifact],
    *,
    selector: ConsolidatedArtifactSelector,
    lang: str,
    archive: ConsolidatedArchiveLike,
) -> CachedConsolidatedArtifact | None:
    """Select one artifact.  Use ``_select_from_cached_artifacts_with_info``
    when caller needs selection provenance."""
    artifact, _ = _select_from_cached_artifacts_with_info(
        artifacts, selector=selector, lang=lang, archive=archive
    )
    return artifact


def _select_from_cached_artifacts_with_info(
    artifacts: list[CachedConsolidatedArtifact],
    *,
    selector: ConsolidatedArtifactSelector,
    lang: str,
    archive: ConsolidatedArchiveLike,
) -> tuple[CachedConsolidatedArtifact | None, SelectionProvenance]:
    """Select one artifact and return a provenance record alongside."""
    original_mode = selector.mode.value if hasattr(selector.mode, "value") else str(selector.mode)
    rejected_tags: list[str] = []
    any_tolerance = False

    if selector.mode == ConsolidatedSelectionMode.BENCH_COMPARABLE:
        comparable: list[CachedConsolidatedArtifact] = []
        for artifact in artifacts:
            ok, tol = _is_self_comparable_with_tolerance(artifact, archive)
            if ok:
                comparable.append(artifact)
                if tol:
                    any_tolerance = True
            else:
                rejected_tags.append(artifact.version_tag)
        if comparable:
            artifacts = comparable
        selector = ConsolidatedArtifactSelector.latest_cached_editorial()

    selected = select_consolidated_record(
        (_cached_artifact_record(artifact, lang=lang) for artifact in artifacts),
        selector,
    )
    if selected is None:
        return None, SelectionProvenance(
            selector_mode=original_mode,
            chosen_version_tag="",
            tolerance_applied=any_tolerance,
            rejected_version_tags=tuple(rejected_tags),
        )
    for artifact in artifacts:
        if artifact.locator == selected.locator:
            prov = SelectionProvenance(
                selector_mode=original_mode,
                chosen_version_tag=artifact.version_tag,
                tolerance_applied=any_tolerance,
                rejected_version_tags=tuple(rejected_tags),
            )
            return artifact, prov
    return None, SelectionProvenance(
        selector_mode=original_mode,
        chosen_version_tag="",
        tolerance_applied=any_tolerance,
        rejected_version_tags=tuple(rejected_tags),
    )


def _version_tag_to_amendment_id(version_tag: str) -> str:
    if not (version_tag.isdigit() and len(version_tag) == 8):
        return ""
    return f"{version_tag[:4]}/{int(version_tag[4:])}"


def _is_self_comparable_cached_artifact(
    artifact: CachedConsolidatedArtifact,
    archive: ConsolidatedArchiveLike,
) -> bool:
    """Return True when an artifact is self-commensurable for bench use.

    Delegates to ``_is_self_comparable_with_tolerance``; callers that need
    the tolerance flag should use that function directly.
    """
    ok, _ = _is_self_comparable_with_tolerance(artifact, archive)
    return ok


def list_cached_consolidated_artifacts(
    archive: ConsolidatedArchiveLike,
    sid: str,
    *,
    lang: str = "fin",
) -> list[CachedConsolidatedArtifact]:
    artifacts: list[CachedConsolidatedArtifact] = []
    for locator in archive.locators(
        build_versioned_consolidated_main_glob(sid=sid, lang=lang)
    ):
        xml = archive.get(locator)
        if xml is None:
            continue
        record = artifact_record(locator, xml)
        version_tag = record.embedded_version_tag
        if not version_tag:
            continue
        canonical_locator = canonical_consolidated_locator(locator, version_tag=version_tag)
        artifacts.append(
            CachedConsolidatedArtifact(
                sid=record.sid or sid,
                locator=locator,
                canonical_locator=canonical_locator,
                xml=xml,
                version_tag=version_tag,
                date_consolidated=record.date_consolidated,
            )
        )
    return artifacts


def select_cached_consolidated_artifact(
    archive: ConsolidatedArchiveLike,
    sid: str,
    *,
    selector: ConsolidatedArtifactSelector | None = None,
    lang: str = "fin",
) -> CachedConsolidatedArtifact | None:
    artifact, _ = select_cached_consolidated_artifact_with_info(
        archive, sid, selector=selector, lang=lang
    )
    return artifact


def select_cached_consolidated_artifact_with_info(
    archive: ConsolidatedArchiveLike,
    sid: str,
    *,
    selector: ConsolidatedArtifactSelector | None = None,
    lang: str = "fin",
) -> tuple[CachedConsolidatedArtifact | None, SelectionProvenance]:
    """Select one artifact and return a :class:`SelectionProvenance` alongside.

    Use this variant when callers need to populate ``OracleSelectorInfo`` on
    ``ReplayResult`` (or any other downstream provenance carrier).
    """
    records = list_cached_consolidated_artifacts(archive, sid, lang=lang)
    if not records:
        eff_selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
        mode_str = eff_selector.mode.value if hasattr(eff_selector.mode, "value") else str(eff_selector.mode)
        return None, SelectionProvenance(selector_mode=mode_str)
    eff_selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    return _select_from_cached_artifacts_with_info(
        records, selector=eff_selector, lang=lang, archive=archive
    )


def best_cached_consolidated_artifact(
    archive: ConsolidatedArchiveLike,
    sid: str,
    *,
    lang: str = "fin",
) -> CachedConsolidatedArtifact | None:
    return select_cached_consolidated_artifact(archive, sid, lang=lang)


def select_cached_consolidated_path_index(
    archive: ConsolidatedArchiveLike,
    *,
    selector: ConsolidatedArtifactSelector | None = None,
    lang: str = "fin",
) -> dict[str, str]:
    selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    candidates: dict[str, list[CachedConsolidatedArtifact]] = {}
    for locator in archive.locators(
        build_versioned_consolidated_main_glob(lang=lang)
    ):
        xml = archive.get(locator)
        if xml is None:
            continue
        record = artifact_record(locator, xml)
        if not record.embedded_version_tag or not record.sid:
            continue
        artifact = CachedConsolidatedArtifact(
            sid=record.sid,
            locator=locator,
            canonical_locator=canonical_consolidated_locator(
                locator,
                version_tag=record.embedded_version_tag,
            ),
            xml=xml,
            version_tag=record.embedded_version_tag,
            date_consolidated=record.date_consolidated,
        )
        candidates.setdefault(record.sid, []).append(artifact)

    result: dict[str, str] = {}
    for sid, artifacts in candidates.items():
        selected = _select_from_cached_artifacts(
            artifacts,
            selector=selector,
            lang=lang,
            archive=archive,
        )
        if selected is not None:
            result[sid] = selected.canonical_locator
    return result


def best_cached_consolidated_path_index(
    archive: ConsolidatedArchiveLike,
    *,
    lang: str = "fin",
) -> dict[str, str]:
    """Build sid → best locator index.

    Return canonical locators derived from each artifact's embedded identity.
    The path suffix can be an editorial/cache version that differs from the
    legal FRBR identity, so this uses the same selector path as other cached
    consolidated artifact APIs.
    """
    return select_cached_consolidated_path_index(archive, lang=lang)
