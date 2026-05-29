from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_selection import VersionSelectionCertificate, VersionSelectionResult


def _address() -> LegalAddress:
    return LegalAddress(path=(("section", "1"),))


def _version(*, effective: str = "2024-01-01", enacted: str = "2023-12-01") -> ProvisionVersion:
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Selected text."),
    )


def test_version_selection_certificate_normalizes_required_dimensions() -> None:
    cert = VersionSelectionCertificate(
        address=_address(),
        as_of="2024-06-01",
        query_type="governing",
        selected_rail="ambiguous_missing_scope",
        candidate_count=2,
        required_dimensions=cast(Any, ["territory"]),
    )

    assert cert.required_dimensions == ("territory",)


def test_version_selection_certificate_rejects_invalid_rail() -> None:
    with pytest.raises(ValueError, match="selected_rail"):
        VersionSelectionCertificate(
            address=_address(),
            as_of="2024-06-01",
            query_type="governing",
            selected_rail="list_order",
        )


def test_version_selection_certificate_rejects_negative_candidate_count() -> None:
    with pytest.raises(ValueError, match="candidate_count"):
        VersionSelectionCertificate(
            address=_address(),
            as_of="2024-06-01",
            query_type="governing",
            candidate_count=-1,
        )


def test_version_selection_result_rejects_selected_without_version() -> None:
    with pytest.raises(ValueError, match="requires a version"):
        VersionSelectionResult(status="selected")


def test_version_selection_result_rejects_certificate_version_drift() -> None:
    version = _version(effective="2024-01-01")
    cert = VersionSelectionCertificate(
        address=_address(),
        as_of="2024-06-01",
        query_type="governing",
        selected_rail="background",
        candidate_count=1,
        selected_effective="2024-02-01",
        selected_enacted=version.enacted,
    )

    with pytest.raises(ValueError, match="selected_effective"):
        VersionSelectionResult(status="selected", version=version, certificate=cert)


def test_version_selection_result_rejects_ambiguous_without_scope_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        VersionSelectionResult(status="ambiguous_missing_scope")


def test_version_selection_result_rejects_absent_with_version() -> None:
    with pytest.raises(ValueError, match="non-selected"):
        VersionSelectionResult(status="absent", version=_version())
