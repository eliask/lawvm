"""Version-selection helpers and carriers for timeline queries."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash

_VERSION_SELECTION_STATUSES = frozenset({"selected", "absent", "ambiguous_missing_scope"})
_VERSION_SELECTION_RAILS = frozenset(
    {"overlay", "background", "absent", "ambiguous_missing_scope"}
)
_QUERY_TYPES = frozenset({"governing", "in_force"})
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BASE_SENTINEL_DATE = "0000-00-00"
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR = (
    "lawvm_materialize_as_absent_under_detached_horizon"
)


def _validate_query_type(query_type: str) -> None:
    if not isinstance(query_type, str) or query_type not in _QUERY_TYPES:
        raise ValueError(f"query_type must be one of {sorted(_QUERY_TYPES)!r}")


def _validate_query_date(value: str, *, field: str) -> None:
    if value == _BASE_SENTINEL_DATE:
        return
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise ValueError(f"{field} must be an exact YYYY-MM-DD date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a real calendar date") from exc


def _validate_selection_query(
    *,
    as_of: str,
    query_type: str,
    expires_as_of: str = "",
) -> None:
    _validate_query_date(as_of, field="as_of")
    if expires_as_of:
        _validate_query_date(expires_as_of, field="expires_as_of")
    _validate_query_type(query_type)


@dataclass(frozen=True, slots=True)
class VersionSelectionCoverage:
    """Positive certificate explaining one version-selection decision."""

    address: LegalAddress
    as_of: str
    query_type: str
    territory: Optional[str] = None
    selected_rail: str = "absent"
    candidate_count: int = 0
    selected_effective: str = ""
    selected_enacted: str = ""
    required_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.address, LegalAddress):
            raise TypeError("VersionSelectionCoverage.address must be LegalAddress")
        if not isinstance(self.as_of, str) or not self.as_of:
            raise ValueError("VersionSelectionCoverage.as_of must be a non-empty string")
        if not isinstance(self.query_type, str) or not self.query_type:
            raise ValueError("VersionSelectionCoverage.query_type must be a non-empty string")
        if self.territory is not None and not isinstance(self.territory, str):
            raise TypeError("VersionSelectionCoverage.territory must be a string or None")
        if self.selected_rail not in _VERSION_SELECTION_RAILS:
            raise ValueError(
                "VersionSelectionCoverage.selected_rail must be one of "
                f"{sorted(_VERSION_SELECTION_RAILS)!r}"
            )
        if not isinstance(self.candidate_count, int) or isinstance(self.candidate_count, bool):
            raise TypeError("VersionSelectionCoverage.candidate_count must be an integer")
        if self.candidate_count < 0:
            raise ValueError("VersionSelectionCoverage.candidate_count must be non-negative")
        if not isinstance(self.selected_effective, str):
            raise TypeError("VersionSelectionCoverage.selected_effective must be a string")
        if not isinstance(self.selected_enacted, str):
            raise TypeError("VersionSelectionCoverage.selected_enacted must be a string")
        object.__setattr__(self, "required_dimensions", tuple(self.required_dimensions))
        if any(not isinstance(dimension, str) or not dimension for dimension in self.required_dimensions):
            raise ValueError(
                "VersionSelectionCoverage.required_dimensions must contain non-empty strings"
            )
        if self.selected_rail in {"overlay", "background"} and not self.selected_effective:
            raise ValueError(
                "VersionSelectionCoverage.selected_effective is required for selected rails"
            )


@dataclass(frozen=True, slots=True)
class VersionSelectionResult:
    """Explicit selection result that can represent missing required scope."""

    selection_status: str
    version: Optional[ProvisionVersion] = None
    required_dimensions: tuple[str, ...] = ()
    certificate: Optional[VersionSelectionCoverage] = None

    def __post_init__(self) -> None:
        if self.selection_status not in _VERSION_SELECTION_STATUSES:
            raise ValueError(
                "VersionSelectionResult.selection_status must be one of "
                f"{sorted(_VERSION_SELECTION_STATUSES)!r}"
            )
        if self.version is not None and not isinstance(self.version, ProvisionVersion):
            raise TypeError("VersionSelectionResult.version must be ProvisionVersion or None")
        object.__setattr__(self, "required_dimensions", tuple(self.required_dimensions))
        if any(not isinstance(dimension, str) or not dimension for dimension in self.required_dimensions):
            raise ValueError(
                "VersionSelectionResult.required_dimensions must contain non-empty strings"
            )
        if self.certificate is not None and not isinstance(
            self.certificate, VersionSelectionCoverage
        ):
            raise TypeError(
                "VersionSelectionResult.certificate must be VersionSelectionCoverage or None"
            )
        if self.selection_status == "selected":
            if self.version is None:
                raise ValueError("VersionSelectionResult selected status requires a version")
            if self.certificate is not None:
                if self.certificate.selected_rail not in {"overlay", "background"}:
                    raise ValueError(
                        "VersionSelectionResult selected certificate must use overlay/background rail"
                    )
                if self.certificate.selected_effective != self.version.effective:
                    raise ValueError(
                        "VersionSelectionResult certificate selected_effective "
                        "must match version.effective"
                    )
                if self.certificate.selected_enacted != self.version.enacted:
                    raise ValueError(
                        "VersionSelectionResult certificate selected_enacted "
                        "must match version.enacted"
                    )
            return
        if self.version is not None:
            raise ValueError("VersionSelectionResult non-selected status cannot carry a version")
        if self.selection_status == "ambiguous_missing_scope" and not self.required_dimensions:
            raise ValueError(
                "VersionSelectionResult ambiguous_missing_scope requires required_dimensions"
            )
        if self.certificate is not None and self.certificate.selected_rail != self.selection_status:
            raise ValueError(
                "VersionSelectionResult non-selected certificate rail must match result status"
            )


@dataclass(frozen=True, slots=True)
class VersionSelectionTie:
    """Equal-rank active candidates where current selection would need list order."""

    address: LegalAddress
    effective: str
    enacted: str
    source_statute: str
    variant_kind: str
    candidate_count: int


@dataclass(frozen=True, slots=True)
class _VersionSelectionConflictKey:
    """Equal-rank dimensions for same-source version-selection conflict checks."""

    variant_kind: str
    effective: str
    enacted: str
    source_statute: str


def _day_before_iso(iso_date: str) -> str:
    """ISO date one day before ``iso_date``; passthrough for non-date strings.

    Used to name the LAST in-force day of a version under the exclusive
    ``expires`` convention (in force on [effective, expires), so the last
    in-force day is ``expires - 1``).
    """
    import datetime as _dt

    try:
        return (_dt.date.fromisoformat(iso_date) - _dt.timedelta(days=1)).isoformat()
    except ValueError:
        return iso_date


def content_is_repeal_placeholder(content: IRNode | None) -> bool:
    """Return whether timeline content is a repeal placeholder node."""
    if content is None:
        return False
    return content.attrs.get("lawvm_repeal_placeholder") == "1"


def _projects_as_absent_under_detached_horizon(content: IRNode | None) -> bool:
    if content is None:
        return False
    return content.attrs.get(_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR) == "1"


def eligible(
    v: ProvisionVersion,
    as_of: str,
    query_type: str,
    expires_as_of: str = "",
) -> bool:
    """Check if a version is temporally eligible at as_of."""
    expiry_horizon = expires_as_of or as_of
    return (
        v.effective <= as_of
        and (query_type != "in_force" or not v.enacted or v.enacted <= as_of)
        and (not v.expires or v.expires > expiry_horizon)
    )


def pick_latest(versions: list[ProvisionVersion]) -> Optional[ProvisionVersion]:
    """Pick the latest version by (effective, enacted, substantive-bias, index)."""
    if not versions:
        return None
    if len(versions) == 1:
        return versions[0]

    same_source_late_placeholder_ties: set[tuple[str, str, str]] = set()
    indexed: list[tuple[int, ProvisionVersion, bool, tuple[str, str, str]]] = []
    max_placeholder_index_by_key: dict[tuple[str, str, str], int] = {}
    min_substantive_index_by_key: dict[tuple[str, str, str], int] = {}
    for idx, version in enumerate(versions):
        source_statute = version.source.statute_id if version.source is not None else ""
        key = (version.effective, version.enacted, source_statute)
        is_placeholder = content_is_repeal_placeholder(version.content)
        indexed.append((idx, version, is_placeholder, key))
        if is_placeholder:
            current = max_placeholder_index_by_key.get(key)
            if current is None or idx > current:
                max_placeholder_index_by_key[key] = idx
        else:
            current = min_substantive_index_by_key.get(key)
            if current is None or idx < current:
                min_substantive_index_by_key[key] = idx
    for key, max_placeholder_index in max_placeholder_index_by_key.items():
        min_substantive_index = min_substantive_index_by_key.get(key)
        if min_substantive_index is not None and max_placeholder_index > min_substantive_index:
            same_source_late_placeholder_ties.add(key)

    return max(
        indexed,
        key=lambda item: (
            item[1].effective,
            item[1].enacted,
            2
            if (
                item[2]
                and item[3] in same_source_late_placeholder_ties
            )
            else (0 if item[2] else 1),
            item[0],
        ),
    )[1]


def equal_rank_same_source_conflicts(
    timeline: ProvisionTimeline,
    *,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> tuple[VersionSelectionTie, ...]:
    """Return active same-source selection ties with distinct legal content.

    ``pick_latest`` intentionally preserves historical behavior by choosing a
    deterministic winner. This helper exposes cases where that winner still
    depends on equal-rank candidates rather than a proved legal precedence rule.
    """
    _validate_selection_query(
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    return equal_rank_same_source_conflicts_prevalidated(
        timeline,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )


def equal_rank_same_source_conflicts_prevalidated(
    timeline: ProvisionTimeline,
    *,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> tuple[VersionSelectionTie, ...]:
    """Return active same-source ties after caller has validated the query."""
    if len(timeline.versions) < 2:
        return ()

    eligible_versions = [
        version
        for version in timeline.versions
        if (
            eligible(version, as_of, query_type, expires_as_of=expires_as_of)
            and applicability_matches(version, territory=territory)
        )
    ]
    temporary_versions = [
        version for version in eligible_versions if version.variant_kind == "temporary"
    ]
    selection_rail = temporary_versions or [
        version for version in eligible_versions if version.variant_kind == "permanent"
    ]
    if len(selection_rail) < 2:
        return ()

    first_by_key: dict[_VersionSelectionConflictKey, ProvisionVersion] = {}
    key_order: list[_VersionSelectionConflictKey] = []
    duplicate_groups: dict[_VersionSelectionConflictKey, list[ProvisionVersion]] = {}
    for version in selection_rail:
        source_statute = version.source.statute_id if version.source is not None else ""
        key = _VersionSelectionConflictKey(
            variant_kind=version.variant_kind,
            effective=version.effective,
            enacted=version.enacted,
            source_statute=source_statute,
        )
        duplicate_group = duplicate_groups.get(key)
        if duplicate_group is not None:
            duplicate_group.append(version)
            continue
        first_seen = first_by_key.get(key)
        if first_seen is not None:
            duplicate_groups[key] = [first_seen, version]
            continue
        first_by_key[key] = version
        key_order.append(key)
    if not duplicate_groups:
        return ()

    conflicts: list[VersionSelectionTie] = []
    for key in key_order:
        versions = duplicate_groups.get(key)
        if versions is None:
            continue
        content_hashes = {
            irnode_content_hash(version.content) if version.content is not None else "<absent>"
            for version in versions
        }
        if len(content_hashes) < 2:
            continue
        conflicts.append(
            VersionSelectionTie(
                address=timeline.address,
                effective=key.effective,
                enacted=key.enacted,
                source_statute=key.source_statute,
                variant_kind=key.variant_kind,
                candidate_count=len(versions),
            )
        )
    return tuple(conflicts)


def applicability_matches(
    version: ProvisionVersion,
    *,
    territory: Optional[str] = None,
) -> bool:
    """Return True when a version's applicability allows the requested scope."""
    if not version.applicability:
        return True
    territory_preds = [pred for pred in version.applicability if pred.dimension == "territory"]
    if not territory_preds:
        return True
    if territory is None:
        return False
    return any(territory in pred.includes for pred in territory_preds)


def required_scope_dimensions(
    timeline: ProvisionTimeline,
    *,
    as_of: str,
    query_type: str,
    expires_as_of: str = "",
) -> tuple[str, ...]:
    """Return required scope dimensions for active candidates at `as_of`."""
    _validate_selection_query(
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    dims: set[str] = set()
    for version in timeline.versions:
        if not eligible(version, as_of, query_type, expires_as_of=expires_as_of):
            continue
        if any(pred.dimension == "territory" for pred in version.applicability):
            dims.add("territory")
    return tuple(sorted(dims))


def _required_scope_dimensions_from_eligible(
    versions: list[ProvisionVersion],
) -> tuple[str, ...]:
    dims: set[str] = set()
    for version in versions:
        if any(pred.dimension == "territory" for pred in version.applicability):
            dims.add("territory")
    return tuple(sorted(dims))


def _select_background_version_from_eligible(
    versions: list[ProvisionVersion],
    *,
    as_of: str,
    territory: Optional[str],
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    expiry_horizon = expires_as_of or as_of
    if any(
        (
            applicability_matches(v, territory=territory)
            and v.expires
            and v.expires <= expiry_horizon
            and (v.content is None or content_is_repeal_placeholder(v.content))
        )
        for v in versions
    ):
        return None
    return pick_latest(
        [
            v
            for v in versions
            if (
                v.variant_kind == "permanent"
                and applicability_matches(v, territory=territory)
                and not (
                    expires_as_of
                    and as_of > expires_as_of
                    and (v.content is None or content_is_repeal_placeholder(v.content))
                    and not _projects_as_absent_under_detached_horizon(v.content)
                    and v.effective > expires_as_of
                )
            )
        ]
    )


def _select_temporary_version_from_eligible(
    versions: list[ProvisionVersion],
    *,
    territory: Optional[str],
) -> Optional[ProvisionVersion]:
    return pick_latest(
        [
            v
            for v in versions
            if (
                v.variant_kind == "temporary"
                and applicability_matches(v, territory=territory)
            )
        ]
    )


def _source_statute_id(version: ProvisionVersion) -> str:
    if version.source is None:
        return ""
    return version.source.statute_id


def _independent_later_background_supersedes_overlay(
    *,
    overlay: ProvisionVersion,
    background: ProvisionVersion,
) -> bool:
    """Return True when a later act rewrites an active temporary address.

    A temporary same-source overlay may mask its paired background form until
    expiry. A later independently sourced background version is lex posterior
    for the same address and must not be hidden by an older temporary snapshot.
    """
    if background.effective <= overlay.effective:
        return False
    overlay_source = _source_statute_id(overlay)
    background_source = _source_statute_id(background)
    if not overlay_source or not background_source:
        return False
    return overlay_source != background_source


def _select_single_active_version(
    timeline: ProvisionTimeline,
    version: ProvisionVersion,
    *,
    as_of: str,
    query_type: str,
    territory: Optional[str],
    expires_as_of: str,
) -> VersionSelectionResult | None:
    """Fast path for one-version timelines, preserving selector certificates."""
    if not eligible(version, as_of, query_type, expires_as_of=expires_as_of):
        return VersionSelectionResult(
            selection_status="absent",
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="absent",
                candidate_count=0,
            ),
        )

    required_dimensions = _required_scope_dimensions_from_eligible([version])
    if territory is None and required_dimensions:
        return VersionSelectionResult(
            selection_status="ambiguous_missing_scope",
            required_dimensions=required_dimensions,
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="ambiguous_missing_scope",
                candidate_count=1,
                required_dimensions=required_dimensions,
            ),
        )
    if not applicability_matches(version, territory=territory):
        return VersionSelectionResult(
            selection_status="absent",
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="absent",
                candidate_count=1,
            ),
        )

    if version.variant_kind == "temporary":
        selected_rail = "overlay"
    elif version.variant_kind == "permanent":
        expiry_horizon = expires_as_of or as_of
        if (
            version.expires
            and version.expires <= expiry_horizon
            and (
                version.content is None
                or content_is_repeal_placeholder(version.content)
            )
        ) or (
            expires_as_of
            and as_of > expires_as_of
            and (
                version.content is None
                or content_is_repeal_placeholder(version.content)
            )
            and not _projects_as_absent_under_detached_horizon(version.content)
            and version.effective > expires_as_of
        ):
            return VersionSelectionResult(
                selection_status="absent",
                certificate=VersionSelectionCoverage(
                    address=timeline.address,
                    as_of=as_of,
                    query_type=query_type,
                    territory=territory,
                    selected_rail="absent",
                    candidate_count=1,
                ),
            )
        selected_rail = "background"
    else:
        return None

    return VersionSelectionResult(
        selection_status="selected",
        version=version,
        certificate=VersionSelectionCoverage(
            address=timeline.address,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            selected_rail=selected_rail,
            candidate_count=1,
            selected_effective=version.effective,
            selected_enacted=version.enacted,
        ),
    )


def select_background_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    """Select the best non-temporary (permanent/background) version at as_of."""
    _validate_selection_query(
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    eligible_versions = [
        version
        for version in timeline.versions
        if eligible(version, as_of, query_type, expires_as_of=expires_as_of)
    ]
    return _select_background_version_from_eligible(
        eligible_versions,
        as_of=as_of,
        territory=territory,
        expires_as_of=expires_as_of,
    )


def select_temporary_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    """Select the best temporary overlay version active at as_of."""
    _validate_selection_query(
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    eligible_versions = [
        version
        for version in timeline.versions
        if eligible(version, as_of, query_type, expires_as_of=expires_as_of)
    ]
    return _select_temporary_version_from_eligible(
        eligible_versions,
        territory=territory,
    )


def select_active_version_ex(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> VersionSelectionResult:
    """Return an explicit active-version selection result."""
    if not as_of:
        raise ValueError("as_of must be non-empty")
    _validate_selection_query(
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    return select_active_version_ex_prevalidated(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )


def select_active_version_ex_prevalidated(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> VersionSelectionResult:
    """Return active-version selection after caller has validated the query."""
    if len(timeline.versions) == 1:
        fast = _select_single_active_version(
            timeline,
            timeline.versions[0],
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            expires_as_of=expires_as_of,
        )
        if fast is not None:
            return fast
    eligible_versions = [
        version
        for version in timeline.versions
        if eligible(version, as_of, query_type, expires_as_of=expires_as_of)
    ]
    required_dimensions = _required_scope_dimensions_from_eligible(eligible_versions)
    if territory is None and required_dimensions:
        return VersionSelectionResult(
            selection_status="ambiguous_missing_scope",
            required_dimensions=required_dimensions,
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="ambiguous_missing_scope",
                candidate_count=len(eligible_versions),
                required_dimensions=required_dimensions,
            ),
        )

    overlay = _select_temporary_version_from_eligible(
        eligible_versions,
        territory=territory,
    )
    background = _select_background_version_from_eligible(
        eligible_versions,
        as_of=as_of,
        territory=territory,
        expires_as_of=expires_as_of,
    )
    if (
        overlay is not None
        and background is not None
        and (
            _independent_later_background_supersedes_overlay(
                overlay=overlay,
                background=background,
            )
            or (
                overlay.expires
                and background.effective > overlay.effective
                and background.effective >= _day_before_iso(overlay.expires)
            )
        )
    ):
        # Regime-handoff day: a newer permanent version whose effective date
        # falls ON the overlay's LAST in-force day supersedes the overlay for
        # that day (lex posterior). Witness 2016/258 §8: 1199/2021 commences
        # 2021-12-31 — deliberately the same day 1458/2019's temporary text is
        # last in force (exclusive expires 2022-01-01) — and the consolidation
        # shows 1199's text. This deliberately does NOT generalize to
        # same-source mid-window permanent updates: a temporary overlay
        # continues to govern over its paired/deferred background inside its
        # window (two-rail doctrine), and twin windows are untouched. Later
        # independent rewrites of the same address are lex posterior and must
        # not be masked by an older temporary snapshot.
        overlay = None
    if overlay is not None:
        return VersionSelectionResult(
            selection_status="selected",
            version=overlay,
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="overlay",
                candidate_count=len(eligible_versions),
                selected_effective=overlay.effective,
                selected_enacted=overlay.enacted,
            ),
        )

    if background is not None:
        return VersionSelectionResult(
            selection_status="selected",
            version=background,
            certificate=VersionSelectionCoverage(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="background",
                candidate_count=len(eligible_versions),
                selected_effective=background.effective,
                selected_enacted=background.enacted,
            ),
        )

    return VersionSelectionResult(
        selection_status="absent",
        certificate=VersionSelectionCoverage(
            address=timeline.address,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            selected_rail="absent",
            candidate_count=len(eligible_versions),
        ),
    )


def select_active_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
) -> Optional[ProvisionVersion]:
    """Return the most recent active ProvisionVersion at date as_of."""
    if not as_of:
        raise ValueError("as_of must be non-empty")
    selection = select_active_version_ex(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
    )
    if selection.selection_status == "ambiguous_missing_scope":
        raise ValueError(
            "select_active_version requires explicit scope when active candidates "
            f"need {selection.required_dimensions!r}; use select_active_version_ex() "
            "for an explicit ambiguity result."
        )
    version = selection.version
    if version is not None and version.effective > as_of:
        raise AssertionError("returned version (if any) must have effective <= as_of")
    return version
