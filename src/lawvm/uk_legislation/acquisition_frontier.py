"""UK effect-feed acquisition-frontier classification.

Some UK statutes have an effect feed that is entirely absent, unpublished,
empty, returns an HTTP 404, or returns an HTTP 300 "Multiple Choices"
disambiguation page. These are a STOP-HERE *acquisition* frontier — there is no
effect-feed source to replay — rather than a replay gap. This module is a
read-only DIAGNOSTIC sensor: it labels the EXISTING cached/feed state of a
statute with a typed, reason-tagged class so the acquisition frontier becomes
actionable, without fetching from the network or changing any acquisition,
replay, or compile behavior.

The presence/empty/multiple-choices/parse signals were previously detected only
implicitly and scattered across:

  * :func:`lawvm.uk_legislation.source_state.classify_uk_source_blob`
    (Multiple Choices / too-small / absent for *statute* XML);
  * :mod:`lawvm.uk_legislation.effects` parse rejections
    (``uk_effect_feed_empty_recorded`` / ``uk_effect_feed_pages_absent_recorded``
    / ``uk_effect_feed_xml_parse_rejected``);
  * the broad-baseline triage buckets in ``scripts/uk_broad_baseline.py``.

This classifier consolidates those signals into one typed
:class:`UKEffectFeedState` taxonomy and one
:class:`UKAcquisitionFrontierState` carrier. It reuses the existing
Multiple-Choices blob recognizer and the same Atom-entry parse the live feed
loader uses, so it cannot disagree with acquisition about what a blob *is*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lxml import etree as ET

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.uk_legislation.source_state import (
    UKStatuteXmlContentStatus,
    _is_uk_multiple_choices_blob,
)

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ATOM_ENTRY_TAG = f"{{{_ATOM_NS}}}entry"

# An HTTP 404 / 300 page that was nevertheless cached as feed bytes begins with a
# stored status banner (see ``_http_get`` returning the body for non-2xx codes).
# The acquisition layer stores the ``http_<code>`` *status* separately, but a
# blob that is itself an error banner is recognized here so a cached error page
# is not mistaken for an empty Atom feed.
_HTTP_404_BANNER_PREFIXES = (
    "http 404 not found",
    "http 404",
    "404 not found",
)


class UKEffectFeedState(StrEnum):
    """Typed acquisition-frontier state of a statute's effect feed.

    The values are the stable reason-tag vocabulary surfaced in diagnostics and
    reports. ``feed_present_nonempty`` is the only non-frontier state.
    """

    FEED_PRESENT_NONEMPTY = "feed_present_nonempty"
    FEED_EMPTY = "feed_empty"
    FEED_PAGE_ABSENT = "feed_page_absent"
    FEED_HTTP_404 = "feed_http_404"
    FEED_MULTIPLE_CHOICES = "feed_multiple_choices"
    FEED_UNPARSEABLE = "feed_unparseable"
    BASE_METADATA_ONLY = "base_metadata_only"

    @property
    def is_acquisition_frontier(self) -> bool:
        """True when this state is a STOP-HERE acquisition frontier."""
        return self is not UKEffectFeedState.FEED_PRESENT_NONEMPTY


# States that describe the feed blob itself (before the base-XML fallback).
_FEED_BLOB_STATES = frozenset(
    {
        UKEffectFeedState.FEED_PRESENT_NONEMPTY,
        UKEffectFeedState.FEED_EMPTY,
        UKEffectFeedState.FEED_PAGE_ABSENT,
        UKEffectFeedState.FEED_HTTP_404,
        UKEffectFeedState.FEED_MULTIPLE_CHOICES,
        UKEffectFeedState.FEED_UNPARSEABLE,
    }
)

# Feed states where the absence of replayable effect rows could equally be a
# metadata-only base envelope rather than an unpublished feed. ``base_metadata_only``
# is promoted over these when the base XML is itself a NumberOfProvisions=0 shell.
_BASE_METADATA_PROMOTABLE_STATES = frozenset(
    {
        UKEffectFeedState.FEED_EMPTY,
        UKEffectFeedState.FEED_PAGE_ABSENT,
    }
)


@dataclass(frozen=True)
class UKEffectFeedPageState:
    """Classified state of a single cached effect-feed page blob."""

    state: UKEffectFeedState
    size: int
    entry_count: int = 0
    parse_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "state": self.state.value,
            "size": self.size,
            "entry_count": self.entry_count,
        }
        if self.parse_error:
            row["parse_error"] = self.parse_error
        return row


@dataclass(frozen=True)
class UKAcquisitionFrontierState:
    """Typed acquisition-frontier classification for one statute's effect feed.

    ``state`` is the single dominant reason; ``reasons`` is the full ordered set
    of contributing reason tags (stable, de-duplicated). ``page_states`` records
    the per-page blob classification so a diagnosis does not have to re-derive
    why the feed was treated as a frontier.
    """

    statute_id: str
    state: UKEffectFeedState
    reasons: tuple[UKEffectFeedState, ...]
    feed_page_count: int
    total_entry_count: int
    base_source_status: str = ""
    page_states: tuple[UKEffectFeedPageState, ...] = ()

    @property
    def is_acquisition_frontier(self) -> bool:
        return self.state.is_acquisition_frontier

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "state": self.state.value,
            "is_acquisition_frontier": self.is_acquisition_frontier,
            "reasons": [reason.value for reason in self.reasons],
            "feed_page_count": self.feed_page_count,
            "total_entry_count": self.total_entry_count,
            "base_source_status": self.base_source_status,
            "page_states": [page.to_dict() for page in self.page_states],
        }

    def to_diagnostic_detail(self) -> dict[str, Any]:
        """Project the classification as a nonblocking source-pathology record.

        The record is an observation, not a rejection: it does not block replay
        and carries ``strict_disposition='record'``. It exists so strict mode and
        audit surfaces can SEE the acquisition frontier as a typed class.
        """
        return diagnostic_detail(
            rule_id="uk_effect_feed_acquisition_frontier_classified",
            family="source_pathology",
            phase="acquisition",
            reason=_REASON_TEXT[self.state],
            blocking=False,
            detail={
                "statute_id": self.statute_id,
                "acquisition_frontier_state": self.state.value,
                "is_acquisition_frontier": self.is_acquisition_frontier,
                "acquisition_frontier_reasons": [reason.value for reason in self.reasons],
                "feed_page_count": self.feed_page_count,
                "total_entry_count": self.total_entry_count,
                "base_source_status": self.base_source_status,
                "page_states": [page.to_dict() for page in self.page_states],
            },
        )


_REASON_TEXT: dict[UKEffectFeedState, str] = {
    UKEffectFeedState.FEED_PRESENT_NONEMPTY: (
        "UK effect feed is present and carries at least one Atom effect entry; "
        "this statute is not an acquisition frontier."
    ),
    UKEffectFeedState.FEED_EMPTY: (
        "UK effect feed pages parsed but contained no Atom effect entries: the "
        "feed is published-but-empty, a STOP-HERE acquisition frontier."
    ),
    UKEffectFeedState.FEED_PAGE_ABSENT: (
        "UK effect feed pages are absent from the cache/archive for this "
        "statute, a STOP-HERE acquisition frontier."
    ),
    UKEffectFeedState.FEED_HTTP_404: (
        "UK effect feed responded HTTP 404 (cached error banner): the feed "
        "endpoint is unpublished, a STOP-HERE acquisition frontier."
    ),
    UKEffectFeedState.FEED_MULTIPLE_CHOICES: (
        "UK effect feed responded with an HTTP 300 Multiple Choices "
        "disambiguation page: the feed locator is ambiguous, a STOP-HERE "
        "acquisition frontier."
    ),
    UKEffectFeedState.FEED_UNPARSEABLE: (
        "UK effect feed bytes were present but not well-formed Atom XML, a "
        "STOP-HERE acquisition frontier."
    ),
    UKEffectFeedState.BASE_METADATA_ONLY: (
        "UK statute base XML is a NumberOfProvisions=0 metadata-only envelope "
        "with no replayable feed, a STOP-HERE acquisition frontier."
    ),
}


def _is_http_404_banner_blob(blob: bytes) -> bool:
    preview = blob[:256].lstrip(b"\xef\xbb\xbf \t\r\n").decode(
        "utf-8", errors="ignore"
    ).lower()
    return any(preview.startswith(prefix) for prefix in _HTTP_404_BANNER_PREFIXES)


def classify_uk_effect_feed_blob(blob: bytes | None) -> UKEffectFeedPageState:
    """Classify a single cached effect-feed page blob into a typed state.

    This mirrors :func:`lawvm.uk_legislation.source_state.classify_uk_source_blob`
    but for the *effect feed* surface, reusing the same Multiple-Choices blob
    recognizer and the same Atom-entry parse as the live feed loader. It does not
    fetch anything.
    """
    if blob is None:
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_PAGE_ABSENT, size=0
        )
    size = len(blob)
    if size == 0:
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_PAGE_ABSENT, size=0
        )
    if _is_http_404_banner_blob(blob):
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_HTTP_404, size=size
        )
    if _is_uk_multiple_choices_blob(blob):
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_MULTIPLE_CHOICES, size=size
        )
    try:
        root = parse_corpus_xml(blob)
    except ET.XMLSyntaxError as exc:
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_UNPARSEABLE,
            size=size,
            parse_error=str(exc),
        )
    entry_count = sum(1 for el in root.iter(_ATOM_ENTRY_TAG))
    if entry_count == 0:
        return UKEffectFeedPageState(
            state=UKEffectFeedState.FEED_EMPTY, size=size
        )
    return UKEffectFeedPageState(
        state=UKEffectFeedState.FEED_PRESENT_NONEMPTY,
        size=size,
        entry_count=entry_count,
    )


def _dominant_feed_state(
    page_states: tuple[UKEffectFeedPageState, ...],
) -> UKEffectFeedState:
    """Reduce per-page states to one dominant feed-blob state.

    A statute's feed is "present and non-empty" as soon as ANY cached page
    carries an entry. Otherwise the most-informative defect across pages wins, in
    a fixed precedence so the result is deterministic regardless of page order:
    multiple-choices > http_404 > unparseable > empty > page_absent.
    """
    if not page_states:
        return UKEffectFeedState.FEED_PAGE_ABSENT
    if any(
        page.state is UKEffectFeedState.FEED_PRESENT_NONEMPTY for page in page_states
    ):
        return UKEffectFeedState.FEED_PRESENT_NONEMPTY
    precedence = (
        UKEffectFeedState.FEED_MULTIPLE_CHOICES,
        UKEffectFeedState.FEED_HTTP_404,
        UKEffectFeedState.FEED_UNPARSEABLE,
        UKEffectFeedState.FEED_EMPTY,
        UKEffectFeedState.FEED_PAGE_ABSENT,
    )
    present = {page.state for page in page_states}
    for candidate in precedence:
        if candidate in present:
            return candidate
    return UKEffectFeedState.FEED_PAGE_ABSENT


def classify_uk_acquisition_frontier(
    statute_id: str,
    feed_blobs: Sequence[bytes | None],
    *,
    base_source_status: str = "",
) -> UKAcquisitionFrontierState:
    """Classify a statute's effect-feed acquisition-frontier state.

    ``feed_blobs`` is the list of cached effect-feed page blobs (in any order);
    an empty list means no feed pages were cached at all. ``base_source_status``
    is the value of
    :attr:`lawvm.uk_legislation.source_state.UKStatuteXmlContentStatus`
    (e.g. ``"metadata_only"``) for the statute's base XML, when known. It is used
    only to PROMOTE an empty/absent-feed frontier to ``base_metadata_only`` — it
    never overrides a feed that actually responded with a defect (Multiple
    Choices, HTTP 404, unparseable bytes), because those describe the feed
    endpoint itself.

    This function is total: every input maps to exactly one
    :class:`UKEffectFeedState`.
    """
    page_states = tuple(
        classify_uk_effect_feed_blob(blob) for blob in feed_blobs
    )
    feed_state = _dominant_feed_state(page_states)
    total_entry_count = sum(page.entry_count for page in page_states)

    state = feed_state
    base_status = str(base_source_status or "")
    if (
        feed_state in _BASE_METADATA_PROMOTABLE_STATES
        and base_status == UKStatuteXmlContentStatus.METADATA_ONLY.value
    ):
        state = UKEffectFeedState.BASE_METADATA_ONLY

    reasons: list[UKEffectFeedState] = []
    if state is UKEffectFeedState.BASE_METADATA_ONLY:
        # Preserve the underlying feed reason alongside the promoted class so the
        # original feed observation is not lost (AGENTS §1.8 visibility).
        reasons.append(UKEffectFeedState.BASE_METADATA_ONLY)
        reasons.append(feed_state)
    else:
        reasons.append(state)

    return UKAcquisitionFrontierState(
        statute_id=str(statute_id),
        state=state,
        reasons=tuple(dict.fromkeys(reasons)),
        feed_page_count=len(page_states),
        total_entry_count=total_entry_count,
        base_source_status=base_status,
        page_states=page_states,
    )


def acquisition_frontier_state_from_archive(
    statute_id: str,
    archive: Any,
    *,
    base_source_status: str = "",
) -> UKAcquisitionFrontierState:
    """Classify a statute's acquisition frontier from cached archive feed bytes.

    READ-ONLY: this reads the effect-feed page locators already indexed in the
    archive and their cached payload bytes. It never fetches from the network and
    never mutates the archive. Missing payload bytes for an indexed locator are
    classified as ``feed_page_absent`` pages, not silently skipped.
    """
    pattern = f"%/changes/affected/{statute_id}/%"
    rows = archive._conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE ? ORDER BY locator",
        (pattern,),
    ).fetchall()
    feed_blobs: list[bytes | None] = []
    for (url,) in rows:
        feed_blobs.append(archive.get(url))
    return classify_uk_acquisition_frontier(
        statute_id,
        feed_blobs,
        base_source_status=base_source_status,
    )


def uk_acquisition_frontier_states_to_report(
    states: Sequence[UKAcquisitionFrontierState],
) -> dict[str, Any]:
    """Build a deterministic report of acquisition-frontier states.

    Rows are sorted by statute id; the summary counts are sorted by reason value.
    No timestamps or set-ordered content appear in the body, so two runs over the
    same inputs diff empty.
    """
    sorted_states = sorted(states, key=lambda s: s.statute_id)
    counts: dict[str, int] = {}
    frontier_ids: list[str] = []
    for s in sorted_states:
        counts[s.state.value] = counts.get(s.state.value, 0) + 1
        if s.is_acquisition_frontier:
            frontier_ids.append(s.statute_id)
    return {
        "statutes": [s.to_dict() for s in sorted_states],
        "acquisition_frontier_statute_count": len(frontier_ids),
        "acquisition_frontier_statutes": sorted(frontier_ids),
        "state_counts": dict(sorted(counts.items())),
    }
