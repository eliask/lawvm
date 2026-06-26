from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import materialize_pit_ex
from lawvm.core.timeline_selection import VersionSelectionCoverage, VersionSelectionResult


def _address() -> LegalAddress:
    return LegalAddress(path=(("section", "1"),))


def _version(*, effective: str = "2024-01-01", enacted: str = "2023-12-01") -> ProvisionVersion:
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Selected text."),
    )


def test_version_selection_coverage_normalizes_required_dimensions() -> None:
    cert = VersionSelectionCoverage(
        address=_address(),
        as_of="2024-06-01",
        query_type="governing",
        selected_rail="ambiguous_missing_scope",
        candidate_count=2,
        required_dimensions=cast(Any, ["territory"]),
    )

    assert cert.required_dimensions == ("territory",)


def test_version_selection_coverage_rejects_invalid_rail() -> None:
    with pytest.raises(ValueError, match="selected_rail"):
        VersionSelectionCoverage(
            address=_address(),
            as_of="2024-06-01",
            query_type="governing",
            selected_rail="list_order",
        )


def test_version_selection_coverage_rejects_negative_candidate_count() -> None:
    with pytest.raises(ValueError, match="candidate_count"):
        VersionSelectionCoverage(
            address=_address(),
            as_of="2024-06-01",
            query_type="governing",
            candidate_count=-1,
        )


def test_version_selection_result_rejects_selected_without_version() -> None:
    with pytest.raises(ValueError, match="requires a version"):
        VersionSelectionResult(selection_status="selected")


def test_version_selection_result_rejects_certificate_version_drift() -> None:
    version = _version(effective="2024-01-01")
    cert = VersionSelectionCoverage(
        address=_address(),
        as_of="2024-06-01",
        query_type="governing",
        selected_rail="background",
        candidate_count=1,
        selected_effective="2024-02-01",
        selected_enacted=version.enacted,
    )

    with pytest.raises(ValueError, match="selected_effective"):
        VersionSelectionResult(selection_status="selected", version=version, certificate=cert)


def test_version_selection_result_rejects_ambiguous_without_scope_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        VersionSelectionResult(selection_status="ambiguous_missing_scope")


def test_version_selection_result_rejects_absent_with_version() -> None:
    with pytest.raises(ValueError, match="non-selected"):
        VersionSelectionResult(selection_status="absent", version=_version())


def test_materialize_pit_validates_selection_query_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import lawvm.core.timeline as timeline_mod

    base = IRStatute(
        statute_id="synthetic",
        title="Synthetic",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="One"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Two"),
            ),
        ),
    )
    timelines = {
        LegalAddress(path=(("section", "1"),)): ProvisionTimeline(
            address=LegalAddress(path=(("section", "1"),)),
            versions=[_version()],
        ),
        LegalAddress(path=(("section", "2"),)): ProvisionTimeline(
            address=LegalAddress(path=(("section", "2"),)),
            versions=[
                ProvisionVersion(
                    effective="2024-01-01",
                    enacted="2023-12-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="2", text="Selected two."),
                ),
            ],
        ),
    }
    validate_calls = 0
    real_validate = timeline_mod._validate_selection_query

    def counting_validate(*args: Any, **kwargs: Any) -> None:
        nonlocal validate_calls
        validate_calls += 1
        real_validate(*args, **kwargs)

    monkeypatch.setattr(timeline_mod, "_validate_selection_query", counting_validate)

    result = materialize_pit_ex(timelines, "2024-06-01", base=base)

    assert result.materialization_status == "materialized"
    assert validate_calls == 1
