from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.core.statute_facets import is_statute_title_address, replace_statute_title, statute_title_address
from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline import compile_timelines, materialize_pit


def test_statute_title_address_is_empty_path_heading_facet() -> None:
    assert is_statute_title_address(LegalAddress(path=(), special=FacetKind.HEADING))
    assert not is_statute_title_address(LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING))
    assert not is_statute_title_address(LegalAddress(path=(), special=FacetKind.WHOLE_ACT))


def test_replace_statute_title_preserves_body_and_metadata() -> None:
    body = IRNode(kind=IRNodeKind.BODY)
    statute = IRStatute(
        statute_id="ee/test",
        title="Old title",
        body=body,
        metadata={"source": "fixture"},
    )

    updated = replace_statute_title(statute, "New title")

    assert updated.title == "New title"
    assert updated.body is body
    assert updated.metadata == statute.metadata
    assert statute.title == "Old title"


def test_compile_timelines_materializes_statute_title_facet() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Body"),))
    statute = IRStatute(statute_id="ee/title", title="Old title", body=body)
    op = LegalOperation(
        op_id="title_replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=statute_title_address(),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="New title"),
        source=OperationSource(statute_id="ee/amend", enacted="2020-01-01"),
        group_id="g:title",
        witness_rule_id="ee_statute_title_replace",
    )
    temporal_events = (
        TemporalEvent(
            event_id="g:title:commence",
            group_id="g:title",
            kind="commence",
            scope=TemporalScope(target_statute=statute.statute_id),
            effective="2020-01-01",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
            source=OperationSource(statute_id="ee/amend"),
        ),
    )

    timelines = compile_timelines(statute, [op], base_date="2019-01-01", temporal_events=temporal_events)
    before = materialize_pit(timelines, "2019-12-31", base=statute)
    after = materialize_pit(timelines, "2020-01-01", base=statute)

    assert before.title == "Old title"
    assert after.title == "New title"
    assert after.body.children == body.children
