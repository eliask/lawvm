"""Version-selection helpers and carriers for timeline queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import icontract

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash


@dataclass(frozen=True)
class VersionSelectionCertificate:
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


@dataclass(frozen=True)
class VersionSelectionResult:
    """Explicit selection result that can represent missing required scope."""

    status: str
    version: Optional[ProvisionVersion] = None
    required_dimensions: tuple[str, ...] = ()
    certificate: Optional[VersionSelectionCertificate] = None


@dataclass(frozen=True)
class VersionSelectionTie:
    """Equal-rank active candidates where current selection would need list order."""

    address: LegalAddress
    effective: str
    enacted: str
    source_statute: str
    variant_kind: str
    candidate_count: int


def content_is_repeal_placeholder(content: IRNode | None) -> bool:
    """Return whether timeline content is a repeal placeholder node."""
    if content is None:
        return False
    return content.attrs.get("lawvm_repeal_placeholder") == "1"


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

    same_source_late_placeholder_ties: set[tuple[str, str, str]] = set()
    grouped: dict[tuple[str, str, str], list[tuple[int, ProvisionVersion]]] = {}
    for idx, version in enumerate(versions):
        source_statute = version.source.statute_id if version.source is not None else ""
        key = (version.effective, version.enacted, source_statute)
        grouped.setdefault(key, []).append((idx, version))
    for key, group in grouped.items():
        placeholder_indexes = [idx for idx, version in group if content_is_repeal_placeholder(version.content)]
        substantive_indexes = [idx for idx, version in group if not content_is_repeal_placeholder(version.content)]
        if placeholder_indexes and substantive_indexes and max(placeholder_indexes) > min(substantive_indexes):
            same_source_late_placeholder_ties.add(key)

    return max(
        enumerate(versions),
        key=lambda iv: (
            iv[1].effective,
            iv[1].enacted,
            2
            if (
                content_is_repeal_placeholder(iv[1].content)
                and (
                    iv[1].effective,
                    iv[1].enacted,
                    iv[1].source.statute_id if iv[1].source is not None else "",
                )
                in same_source_late_placeholder_ties
            )
            else (0 if content_is_repeal_placeholder(iv[1].content) else 1),
            iv[0],
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
    grouped: dict[tuple[str, str, str, str], list[ProvisionVersion]] = {}
    for version in selection_rail:
        source_statute = version.source.statute_id if version.source is not None else ""
        key = (version.variant_kind, version.effective, version.enacted, source_statute)
        grouped.setdefault(key, []).append(version)

    conflicts: list[VersionSelectionTie] = []
    for (variant_kind, effective, enacted, source_statute), versions in grouped.items():
        if len(versions) < 2:
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
                effective=effective,
                enacted=enacted,
                source_statute=source_statute,
                variant_kind=variant_kind,
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
    dims: set[str] = set()
    for version in timeline.versions:
        if not eligible(version, as_of, query_type, expires_as_of=expires_as_of):
            continue
        if any(pred.dimension == "territory" for pred in version.applicability):
            dims.add("territory")
    return tuple(sorted(dims))


def select_background_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    """Select the best non-temporary (permanent/background) version at as_of."""
    expiry_horizon = expires_as_of or as_of
    if any(
        (
            eligible(v, as_of, query_type, expires_as_of=expires_as_of)
            and applicability_matches(v, territory=territory)
            and v.expires
            and v.expires <= expiry_horizon
            and (v.content is None or content_is_repeal_placeholder(v.content))
        )
        for v in timeline.versions
    ):
        return None
    return pick_latest(
        [
            v
            for v in timeline.versions
            if (
                v.variant_kind == "permanent"
                and eligible(v, as_of, query_type, expires_as_of=expires_as_of)
                and applicability_matches(v, territory=territory)
                and not (
                    expires_as_of
                    and as_of > expires_as_of
                    and (v.content is None or content_is_repeal_placeholder(v.content))
                    and v.effective > expires_as_of
                )
            )
        ]
    )


def select_temporary_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: str = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    """Select the best temporary overlay version active at as_of."""
    return pick_latest(
        [
            v
            for v in timeline.versions
            if (
                v.variant_kind == "temporary"
                and eligible(v, as_of, query_type, expires_as_of=expires_as_of)
                and applicability_matches(v, territory=territory)
            )
        ]
    )


@icontract.require(lambda as_of: as_of, "as_of must be non-empty")
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
    eligible_versions = [
        version
        for version in timeline.versions
        if eligible(version, as_of, query_type, expires_as_of=expires_as_of)
    ]
    required_dimensions = required_scope_dimensions(
        timeline,
        as_of=as_of,
        query_type=query_type,
        expires_as_of=expires_as_of,
    )
    if territory is None and required_dimensions:
        return VersionSelectionResult(
            status="ambiguous_missing_scope",
            required_dimensions=required_dimensions,
            certificate=VersionSelectionCertificate(
                address=timeline.address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail="ambiguous_missing_scope",
                candidate_count=len(eligible_versions),
                required_dimensions=required_dimensions,
            ),
        )

    overlay = select_temporary_version(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )
    if overlay is not None:
        return VersionSelectionResult(
            status="selected",
            version=overlay,
            certificate=VersionSelectionCertificate(
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

    background = select_background_version(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )
    if background is not None:
        return VersionSelectionResult(
            status="selected",
            version=background,
            certificate=VersionSelectionCertificate(
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
        status="absent",
        certificate=VersionSelectionCertificate(
            address=timeline.address,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            selected_rail="absent",
            candidate_count=len(eligible_versions),
        ),
    )


@icontract.require(lambda as_of: as_of, "as_of must be non-empty")
@icontract.ensure(
    lambda as_of, result: result is None or result.effective <= as_of,
    "returned version (if any) must have effective <= as_of",
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
    if selection.status == "ambiguous_missing_scope":
        raise ValueError(
            "select_active_version requires explicit scope when active candidates "
            f"need {selection.required_dimensions!r}; use select_active_version_ex() "
            "for an explicit ambiguity result."
        )
    return selection.version
