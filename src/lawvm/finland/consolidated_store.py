"""Finland-owned access layer for cached consolidated-oracle artifacts."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
import datetime as dt
import logging
from typing import Protocol

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

log = logging.getLogger(__name__)
_SEEN_COLLAPSED_DATE_WARNINGS: set[tuple[str, str, dt.date | None, dt.date]] = set()

# Run-pinned commencement reference date. A single bench run pins one ``as_of``
# (via ``pin_selection_as_of``) so every comparability decision in that run uses
# the same date even if the wall clock crosses midnight mid-run, and so a
# future-dated artifact rejected today cannot become silently accepted tomorrow
# with no code/data change. ``None`` means "use the live wall clock"
# (``dt.date.today()``), preserving prior behaviour outside a pinned run.
# Explicit ``as_of`` arguments always win over this pin.
_RUN_PINNED_AS_OF: dt.date | None = None


def pin_selection_as_of(as_of: dt.date | None) -> dt.date | None:
    """Pin (or clear) the run-wide commencement reference date.

    Returns the previous pin so callers can restore it. ``as_of=None`` clears
    the pin (reverting to the live wall clock).
    """
    global _RUN_PINNED_AS_OF
    previous = _RUN_PINNED_AS_OF
    _RUN_PINNED_AS_OF = as_of
    return previous


def _resolve_as_of(as_of: dt.date | None) -> dt.date:
    """Resolve an effective commencement reference date.

    Precedence: explicit ``as_of`` > run pin > live wall clock.
    """
    if as_of is not None:
        return as_of
    if _RUN_PINNED_AS_OF is not None:
        return _RUN_PINNED_AS_OF
    return dt.date.today()


_ARTIFACT_RECORD_CACHE_MAX = 4096
_ARTIFACT_RECORD_CACHE: OrderedDict[
    tuple[str, int, bytes],
    ConsolidatedArtifactRecord,
] = OrderedDict()


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


def _cached_artifact_record_for_xml(
    locator: str,
    xml: bytes,
) -> ConsolidatedArtifactRecord:
    """Return parsed artifact metadata without retaining XML bytes in the cache."""
    digest = hashlib.blake2b(xml, digest_size=16).digest()
    key = (locator, len(xml), digest)
    cached = _ARTIFACT_RECORD_CACHE.get(key)
    if cached is not None:
        _ARTIFACT_RECORD_CACHE.move_to_end(key)
        return cached
    record = artifact_record(locator, xml)
    _ARTIFACT_RECORD_CACHE[key] = record
    if len(_ARTIFACT_RECORD_CACHE) > _ARTIFACT_RECORD_CACHE_MAX:
        _ARTIFACT_RECORD_CACHE.popitem(last=False)
    return record


def _clear_artifact_record_cache_for_tests() -> None:
    _ARTIFACT_RECORD_CACHE.clear()


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
    *,
    as_of: dt.date | None = None,
) -> tuple[bool, bool]:
    """Return ``(is_comparable, tolerance_applied)`` for bench-comparable check.

    ``tolerance_applied`` is True when the artifact was accepted under the
    180-day Finlex-ahead tolerance (Option Z), i.e. ordering_date is in
    ``(date_consolidated, date_consolidated + 180 days]``.

    ``as_of`` is the reference date against which commencement is tested. A
    single bench run threads one fixed ``as_of`` so selection is reproducible
    (a future-dated artifact rejected today must not become accepted tomorrow
    with no code/data change). Defaults to ``dt.date.today()`` only when no
    reference date is threaded — never on the bench path.
    """
    as_of = _resolve_as_of(as_of)
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

    # The collapsed-dates observation is a data-quality signal about oracle
    # metadata: ``date_consolidated`` lags the artifact's actual ordering_date
    # by a positive gap within the 180-day tolerance.  It is logically
    # orthogonal to whether the amendment has commenced — emit it on both the
    # commenced early-return path and the Finlex-ahead tolerance path so the
    # diagnostic is never silently dropped.  Emitting it is a log-only side
    # effect and must not influence the accept/reject decision.
    _emit_collapsed_dates_observation_if_applicable(artifact, ordering_date)

    if ordering_date <= as_of:
        if expiry_date is not None and expiry_date <= ordering_date:
            return False, False
        return True, False

    tolerance_applied = False
    if artifact.date_consolidated is not None:
        gap_days = (ordering_date - artifact.date_consolidated).days
        if gap_days > 180:
            return False, False
        if gap_days > 0:
            tolerance_applied = True

    if expiry_date is not None and expiry_date <= ordering_date:
        return False, False

    return True, tolerance_applied


def _emit_collapsed_dates_observation_if_applicable(
    artifact: CachedConsolidatedArtifact,
    ordering_date: dt.date,
) -> None:
    """Log the ORACLE_METADATA_COLLAPSED_DATES observation when applicable.

    The condition is a pure data-quality signal: a non-null
    ``date_consolidated`` that trails ``ordering_date`` by a positive gap
    within the 180-day tolerance.  This is independent of whether the
    amendment has commenced; it is a logged warning only and never affects
    the comparability decision.  De-duplicated via
    ``_SEEN_COLLAPSED_DATE_WARNINGS``.
    """
    if artifact.date_consolidated is None:
        return
    gap_days = (ordering_date - artifact.date_consolidated).days
    if not (0 < gap_days <= 180):
        return
    warning_key = (
        artifact.sid,
        artifact.version_tag,
        artifact.date_consolidated,
        ordering_date,
    )
    if warning_key in _SEEN_COLLAPSED_DATE_WARNINGS:
        return
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


def _select_from_cached_artifacts(
    artifacts: list[CachedConsolidatedArtifact],
    *,
    selector: ConsolidatedArtifactSelector,
    lang: str,
    archive: ConsolidatedArchiveLike,
    as_of: dt.date | None = None,
) -> CachedConsolidatedArtifact | None:
    """Select one artifact.  Use ``_select_from_cached_artifacts_with_info``
    when caller needs selection provenance."""
    artifact, _ = _select_from_cached_artifacts_with_info(
        artifacts, selector=selector, lang=lang, archive=archive, as_of=as_of
    )
    return artifact


def _select_from_cached_artifacts_with_info(
    artifacts: list[CachedConsolidatedArtifact],
    *,
    selector: ConsolidatedArtifactSelector,
    lang: str,
    archive: ConsolidatedArchiveLike,
    as_of: dt.date | None = None,
) -> tuple[CachedConsolidatedArtifact | None, SelectionProvenance]:
    """Select one artifact and return a provenance record alongside."""
    original_mode = selector.mode.value if hasattr(selector.mode, "value") else str(selector.mode)
    rejected_tags: list[str] = []
    any_tolerance = False

    if selector.mode == ConsolidatedSelectionMode.BENCH_COMPARABLE:
        comparable: list[CachedConsolidatedArtifact] = []
        for artifact in artifacts:
            ok, tol = _is_self_comparable_with_tolerance(artifact, archive, as_of=as_of)
            if ok:
                comparable.append(artifact)
                if tol:
                    any_tolerance = True
            else:
                rejected_tags.append(artifact.version_tag)
        if not comparable:
            # No artifact is bench-comparable. Narrowing is unconditional: there
            # is no honest oracle to score against, so return None rather than
            # silently falling through to the full, unfiltered list (which would
            # include known-INCOMPARABLE artifacts and report an ordinary score
            # against a bad oracle). Fail loud — no silent fallback.
            return None, SelectionProvenance(
                selector_mode=original_mode,
                chosen_version_tag="",
                tolerance_applied=any_tolerance,
                rejected_version_tags=tuple(rejected_tags),
            )
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
    *,
    as_of: dt.date | None = None,
) -> bool:
    """Return True when an artifact is self-commensurable for bench use.

    Delegates to ``_is_self_comparable_with_tolerance``; callers that need
    the tolerance flag should use that function directly.
    """
    ok, _ = _is_self_comparable_with_tolerance(artifact, archive, as_of=as_of)
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
        record = _cached_artifact_record_for_xml(locator, xml)
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
    as_of: dt.date | None = None,
) -> CachedConsolidatedArtifact | None:
    artifact, _ = select_cached_consolidated_artifact_with_info(
        archive, sid, selector=selector, lang=lang, as_of=as_of
    )
    return artifact


def select_cached_consolidated_artifact_with_info(
    archive: ConsolidatedArchiveLike,
    sid: str,
    *,
    selector: ConsolidatedArtifactSelector | None = None,
    lang: str = "fin",
    as_of: dt.date | None = None,
) -> tuple[CachedConsolidatedArtifact | None, SelectionProvenance]:
    """Select one artifact and return a :class:`SelectionProvenance` alongside.

    Use this variant when callers need to populate ``OracleSelectorInfo`` on
    ``ReplayResult`` (or any other downstream provenance carrier).

    ``as_of`` is the reference date for commencement testing; thread a single
    fixed value for one bench run so selection is reproducible. Defaults to
    ``dt.date.today()`` only at this outermost entry when nothing is threaded.
    """
    as_of = _resolve_as_of(as_of)
    records = list_cached_consolidated_artifacts(archive, sid, lang=lang)
    if not records:
        eff_selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
        mode_str = eff_selector.mode.value if hasattr(eff_selector.mode, "value") else str(eff_selector.mode)
        return None, SelectionProvenance(selector_mode=mode_str)
    eff_selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    return _select_from_cached_artifacts_with_info(
        records, selector=eff_selector, lang=lang, archive=archive, as_of=as_of
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
    as_of: dt.date | None = None,
) -> dict[str, str]:
    as_of = _resolve_as_of(as_of)
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
            as_of=as_of,
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
