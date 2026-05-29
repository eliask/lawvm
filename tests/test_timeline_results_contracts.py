from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_results import (
    MaterializationCertificate,
    MaterializationResult,
    TimelineCompilationResult,
    TimelineIssue,
)


def _address() -> LegalAddress:
    return LegalAddress(path=(("section", "1"),))


def _statute() -> IRStatute:
    return IRStatute(
        statute_id="test/results",
        title="Results",
        body=IRNode(kind=IRNodeKind.BODY),
    )


def test_timeline_issue_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        TimelineIssue(kind=cast(Any, "python_order_won"), message="unowned selection")


def test_materialization_certificate_normalizes_required_dimensions() -> None:
    certificate = MaterializationCertificate(
        as_of="2024-01-01",
        query_type="governing",
        required_dimensions=cast(Any, ["territory"]),
    )

    assert certificate.required_dimensions == ("territory",)


def test_materialization_result_rejects_materialized_with_blocking_issue() -> None:
    issue = TimelineIssue(kind="missing_replace_target", message="target missing")

    with pytest.raises(ValueError, match="blocking issues"):
        MaterializationResult(status="materialized", statute=_statute(), issues=(issue,))


def test_materialization_result_rejects_missing_scope_without_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        MaterializationResult(status="degraded_missing_scope", statute=_statute())


def test_materialization_result_rejects_certificate_count_drift() -> None:
    certificate = MaterializationCertificate(
        as_of="2024-01-01",
        query_type="governing",
        ambiguous_address_count=2,
        required_dimensions=("territory",),
    )

    with pytest.raises(ValueError, match="ambiguous_address_count"):
        MaterializationResult(
            status="degraded_missing_scope",
            statute=_statute(),
            required_dimensions=("territory",),
            ambiguous_addresses=(_address(),),
            certificate=certificate,
        )


def test_timeline_compilation_result_freezes_timeline_mapping() -> None:
    address = _address()
    timelines = {address: ProvisionTimeline(address=address)}
    result = TimelineCompilationResult(timelines=timelines)

    timelines[LegalAddress(path=(("section", "2"),))] = ProvisionTimeline(
        address=LegalAddress(path=(("section", "2"),))
    )

    assert tuple(result.timelines) == (address,)
    with pytest.raises(TypeError):
        cast(Any, result.timelines)[LegalAddress(path=(("section", "3"),))] = ProvisionTimeline(
            address=LegalAddress(path=(("section", "3"),))
        )


def test_timeline_compilation_result_rejects_mismatched_mapping_key() -> None:
    with pytest.raises(ValueError, match="mapping key"):
        TimelineCompilationResult(
            timelines={
                _address(): ProvisionTimeline(address=LegalAddress(path=(("section", "2"),)))
            }
        )
