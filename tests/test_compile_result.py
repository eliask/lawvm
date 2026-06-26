"""Tests for compile_result module -- CanonicalBundle purity guards.

Covers the TypeError enforcement layer that prevents frontend-local types
(e.g. Finland's ResolvedOp) from entering the shared CanonicalBundle as
first-class structural_ops payload.

Run:
    uv run pytest tests/test_compile_result.py -v
"""

from __future__ import annotations

import pytest
from typing import Any, cast

from types import SimpleNamespace

from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    EffectRelationTargetResolution,
    SourceInstrumentRef,
    SourceProvisionRef,
    append_unique_effect_lifecycle_event,
    append_unique_effect_ref,
    append_unique_effect_relation,
    effect_graph_wire,
    lower_lifecycle_events_to_temporal_events,
)
from lawvm.core.compile_result import (
    AdmissibleBindingCoverage,
    CanonicalBundle,
    CanonicalEffect,
    CompiledOpEvidenceRow,
    CompiledOpProvenanceTags,
    CompiledOpScopeWitness,
    StrictProfile,
    CompileFailure,
    CompileVerdict,
    SectionStrictVerdict,
    SourceCompletenessInfo,
    SourcePathology,
    _compiled_op_scope_witness,
    _compiled_op_source_statute,
    _compiled_op_matches_section,
    _compiled_op_provenance_tag_sets,
    _operation_matches_section,
    _validate_bundle_purity,
    strict_fail_reasons_from_finding_ledger,
    strict_fail_reasons_from_findings_and_verdict,
)
from lawvm.core.phase_result import Finding, VIOLATION_ROLE
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.core.ir import (
    IRNode,
    IRNodeKind,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
    StructuralAction,
)
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import FacetKind
from lawvm.replay_adjudication import CompileAdjudication, SourceAdjudication
from lawvm.core.target_scope import normalize_target_unit_kind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legal_op(op_id: str = "op-1", section: str = "1") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section),)),
    )


def _runtime_input(value: object) -> Any:
    """Expose deliberately non-static constructor inputs to runtime validators."""
    return cast(Any, value)


def _runtime_structural_ops(*ops: object) -> tuple[LegalOperation, ...]:
    """Bypass static purity so CanonicalBundle's runtime guard is exercised."""
    return cast(tuple[LegalOperation, ...], ops)


class _FrontendLocalOp:
    """Placeholder for a frontend-local type (e.g. Finland's ResolvedOp)."""

    op_id = "frontend-local"


def _impure_bundle(*, structural_ops: tuple[object, ...]) -> CanonicalBundle:
    bundle = object.__new__(CanonicalBundle)
    object.__setattr__(bundle, "source_statute", "")
    object.__setattr__(bundle, "target_statute", "")
    object.__setattr__(bundle, "structural_ops", structural_ops)
    object.__setattr__(bundle, "temporal_events", ())
    object.__setattr__(bundle, "migration_events", ())
    object.__setattr__(bundle, "source_effects", ())
    object.__setattr__(bundle, "effect_relations", ())
    object.__setattr__(bundle, "effect_lifecycle_events", ())
    object.__setattr__(bundle, "effects", ())
    object.__setattr__(bundle, "groups", ())
    object.__setattr__(bundle, "source", None)
    return bundle


def test_legal_address_and_scope_predicate_normalize_sequence_inputs() -> None:
    address = LegalAddress(path=_runtime_input([("section", "1"), ("subsection", "2")]))
    predicate = ScopePredicate(dimension="territory", includes=_runtime_input(["AX", ""]))

    assert isinstance(address.path, tuple)
    assert address.path == (("section", "1"), ("subsection", "2"))
    assert predicate.includes == frozenset({"AX"})


def test_legal_operation_and_provision_version_normalize_sequence_inputs() -> None:
    op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        applicability=_runtime_input(
            [ScopePredicate(dimension="territory", includes=_runtime_input({"AX"}))]
        ),
        provenance_tags=_runtime_input(["tag-a", ""]),
    )
    version = ProvisionVersion(
        effective="2020-01-01",
        applicability=_runtime_input(
            [ScopePredicate(dimension="territory", includes=_runtime_input({"AX"}))]
        ),
    )

    assert isinstance(op.applicability, tuple)
    assert op.provenance_tags == ("tag-a", "")
    assert isinstance(version.applicability, tuple)


def test_strict_profile_rejects_non_boolean_flags() -> None:
    with pytest.raises(ValueError, match="allows_target_guessing"):
        StrictProfile(name="strict", allows_target_guessing=cast(Any, "false"))


def test_source_completeness_info_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError, match="source_available"):
        SourceCompletenessInfo(chain_length=2, source_available=3, dates_available=1)


def test_effect_relation_requires_source_witness_and_target_identifier() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1 §",))

    with pytest.raises(ValueError, match="target_effect or target_instrument"):
        EffectRelation(
            relation_id="rel:1",
            kind="extends_effect_expiry",
            source_provision=witness,
        )
    with pytest.raises(ValueError, match="exactly one target endpoint"):
        EffectRelation(
            relation_id="rel:both",
            kind="extends_effect_expiry",
            source_provision=witness,
            target_effect=EffectRef(effect_id="effect:target", source_instrument=instrument),
            target_instrument=SourceInstrumentRef(instrument_id="2019/1"),
        )

    relation = EffectRelation(
        relation_id="rel:2",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_instrument=SourceInstrumentRef(instrument_id="2019/1"),
    )

    assert relation.source_provision.witness_id == "2020/1:1 §"
    assert relation.target_instrument is not None
    assert relation.target_instrument.instrument_id == "2019/1"


def test_effect_source_refs_reject_untyped_identity_fields() -> None:
    with pytest.raises(TypeError, match="instrument_id"):
        SourceInstrumentRef(instrument_id=cast(Any, 2020))
    with pytest.raises(TypeError, match="title"):
        SourceInstrumentRef(instrument_id="2020/1", title=cast(Any, object()))

    instrument = SourceInstrumentRef(instrument_id=" 2020/1 ")
    assert instrument.instrument_id == "2020/1"

    with pytest.raises(TypeError, match="path"):
        SourceProvisionRef(instrument=instrument, path=cast(Any, (1,)))
    with pytest.raises(TypeError, match="span_id"):
        SourceProvisionRef(instrument=instrument, span_id=cast(Any, object()))
    with pytest.raises(TypeError, match="text_excerpt"):
        SourceProvisionRef(instrument=instrument, text_excerpt=cast(Any, object()))

    witness = SourceProvisionRef(
        instrument=instrument,
        path=(" 1 § ",),
        text_excerpt="  exact witness text  ",
    )
    assert witness.path == ("1 §",)
    assert witness.text_excerpt == "  exact witness text  "


def test_effect_lifecycle_carriers_reject_untyped_identity_fields() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id=" effect:1 ", source_instrument=instrument)

    assert effect.effect_id == "effect:1"

    with pytest.raises(TypeError, match="effect_id"):
        EffectRef(effect_id=cast(Any, 1), source_instrument=instrument)
    with pytest.raises(TypeError, match="target_statute"):
        EffectRef(
            effect_id="effect:target",
            source_instrument=instrument,
            target_statute=cast(Any, object()),
        )
    with pytest.raises(TypeError, match="projection_group_id"):
        EffectRef(
            effect_id="effect:group",
            source_instrument=instrument,
            projection_group_id=cast(Any, object()),
        )

    relation = EffectRelation(
        relation_id=" relation:1 ",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    assert relation.relation_id == "relation:1"

    with pytest.raises(TypeError, match="relation_id"):
        EffectRelation(
            relation_id=cast(Any, 1),
            kind="extends_effect_expiry",
            source_provision=witness,
            target_effect=effect,
        )

    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id=" lifecycle:1 ",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires=" 2021-12-31 ",
        intended_lifecycle_kind=_runtime_input(" change_effect_expiry "),
        intended_relation_kind=_runtime_input(" extends_effect_expiry "),
    )
    assert lifecycle.lifecycle_event_id == "lifecycle:1"
    assert lifecycle.expires == "2021-12-31"
    assert lifecycle.intended_lifecycle_kind == "change_effect_expiry"
    assert lifecycle.intended_relation_kind == "extends_effect_expiry"

    with pytest.raises(TypeError, match="lifecycle_event_id"):
        EffectLifecycleEvent(
            lifecycle_event_id=cast(Any, 1),
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2021-12-31",
        )
    with pytest.raises(TypeError, match="effective"):
        EffectLifecycleEvent(
            lifecycle_event_id="lifecycle:bad-effective",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            effective=cast(Any, object()),
            expires="2021-12-31",
        )
    with pytest.raises(TypeError, match="executable"):
        EffectLifecycleEvent(
            lifecycle_event_id="lifecycle:bad-executable",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2021-12-31",
            executable=cast(Any, "true"),
        )
    with pytest.raises(ValueError, match="intended_lifecycle_kind"):
        EffectLifecycleEvent(
            lifecycle_event_id="lifecycle:bad-intended-kind",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2021-12-31",
            intended_lifecycle_kind=cast(Any, "not-a-lifecycle-kind"),
        )
    with pytest.raises(ValueError, match="intended_relation_kind"):
        EffectLifecycleEvent(
            lifecycle_event_id="lifecycle:bad-intended-relation",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2021-12-31",
            intended_relation_kind=cast(Any, "not-a-relation-kind"),
        )


def test_effect_lifecycle_detail_requires_string_keyed_mappings() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)

    with pytest.raises(TypeError, match="EffectRelation.detail"):
        EffectRelation(
            relation_id="relation:bad-detail",
            kind="extends_effect_expiry",
            source_provision=witness,
            target_effect=effect,
            detail=cast(Any, (("reason", "not-a-mapping"),)),
        )
    with pytest.raises(TypeError, match="keys must be strings"):
        EffectRelation(
            relation_id="relation:bad-key",
            kind="extends_effect_expiry",
            source_provision=witness,
            target_effect=effect,
            detail=cast(Any, {1: "not-a-string-key"}),
        )

    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    with pytest.raises(TypeError, match="keys must be strings"):
        EffectLifecycleEvent(
            lifecycle_event_id="lifecycle:bad-nested-key",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2021-12-31",
            detail=cast(Any, {"rows": [{1: "not-a-string-key"}]}),
        )


def test_effect_relation_target_resolution_is_typed_and_endpoint_consistent() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument)
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)

    resolved = EffectRelation(
        relation_id="relation:effect",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=effect,
    )
    assert resolved.target_resolution is not None
    assert resolved.target_resolution.kind == "target_effect_resolved"
    assert resolved.target_resolution.matched_effect_count == 1

    ambiguous = EffectRelation(
        relation_id="relation:ambiguous",
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=instrument,
        target_resolution=EffectRelationTargetResolution(
            kind="ambiguous_multiple_effects",
            matched_effect_count=2,
            non_executable_reason="multiple source effects match",
        ),
    )
    assert ambiguous.target_resolution is not None
    assert ambiguous.target_resolution.kind == "ambiguous_multiple_effects"
    assert ambiguous.target_resolution.non_executable_reason == "multiple source effects match"

    with pytest.raises(ValueError, match="at least two matched effects"):
        EffectRelationTargetResolution(kind="ambiguous_multiple_effects", matched_effect_count=1)
    with pytest.raises(ValueError, match="target_effect requires target_effect_resolved"):
        EffectRelation(
            relation_id="relation:mismatch",
            kind="modifies_effect",
            source_provision=witness,
            target_effect=effect,
            target_resolution=EffectRelationTargetResolution(kind="target_instrument_only"),
        )


def test_canonical_bundle_requires_source_effect_records() -> None:
    with pytest.raises(TypeError, match="source_effects"):
        CanonicalBundle(source_effects=cast(Any, ("not-an-effect",)))


def test_canonical_bundle_requires_temporal_event_records_and_normalizes_sequence() -> None:
    temporal = TemporalEvent(
        event_id="temporal:1",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-01-01",
    )
    bundle = CanonicalBundle(temporal_events=cast(Any, [temporal]))

    assert bundle.temporal_events == (temporal,)
    with pytest.raises(TypeError, match="temporal_events"):
        CanonicalBundle(temporal_events=cast(Any, ("not-a-temporal-event",)))


def test_canonical_bundle_rejects_duplicate_effect_graph_ids() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect_a = EffectRef(effect_id="effect:1", source_instrument=instrument)
    effect_b = EffectRef(effect_id="effect:1", source_instrument=instrument)
    target_effect = EffectRef(effect_id="effect:target", source_instrument=instrument)
    relation_a = EffectRelation(
        relation_id="relation:1",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    relation_b = EffectRelation(
        relation_id="relation:1",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    lifecycle_a = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )
    lifecycle_b = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )

    with pytest.raises(ValueError, match="duplicate effect_id"):
        CanonicalBundle(source_effects=(effect_a, effect_b))
    with pytest.raises(ValueError, match="duplicate relation_id"):
        CanonicalBundle(effect_relations=(relation_a, relation_b))
    with pytest.raises(ValueError, match="duplicate lifecycle_event_id"):
        CanonicalBundle(effect_lifecycle_events=(lifecycle_a, lifecycle_b))


def test_canonical_bundle_requires_closed_effect_graph() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2021-01-01",
    )

    with pytest.raises(ValueError, match="missing target_effect"):
        CanonicalBundle(effect_relations=(relation,))
    with pytest.raises(ValueError, match="missing relation"):
        CanonicalBundle(
            source_effects=(effect,),
            effect_lifecycle_events=(lifecycle,),
        )


def test_canonical_bundle_rejects_stale_effect_graph_endpoint_records() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    graph_effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    stale_effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "2"),)),
    )
    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=stale_effect,
    )
    graph_relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=graph_effect,
    )
    relation_with_detail = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=graph_effect,
        detail={"note": "stale"},
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=graph_effect,
        relation=relation_with_detail,
        expires="2021-01-01",
    )

    with pytest.raises(ValueError, match="target_effect differs from graph effect"):
        CanonicalBundle(
            source_effects=(graph_effect,),
            effect_relations=(relation,),
        )
    with pytest.raises(ValueError, match="relation differs from graph relation"):
        CanonicalBundle(
            source_effects=(graph_effect,),
            effect_relations=(graph_relation,),
            effect_lifecycle_events=(lifecycle,),
        )


def test_effect_graph_merge_helpers_reject_untyped_records() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        source_provision=witness,
    )
    relation = EffectRelation(
        relation_id="relation:1",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )

    with pytest.raises(TypeError, match="EffectRef"):
        append_unique_effect_ref([], cast(Any, "effect:1"), subject="test source effects")
    with pytest.raises(TypeError, match="EffectRelation"):
        append_unique_effect_relation([], cast(Any, "relation:1"), subject="test relations")
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        append_unique_effect_lifecycle_event([], cast(Any, "event:1"), subject="test lifecycle")

    with pytest.raises(TypeError, match="EffectRef"):
        append_unique_effect_ref(
            cast(Any, ["effect:old"]),
            effect,
            subject="test source effects",
        )
    with pytest.raises(TypeError, match="EffectRelation"):
        append_unique_effect_relation(
            cast(Any, ["relation:old"]),
            relation,
            subject="test relations",
        )
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        append_unique_effect_lifecycle_event(
            cast(Any, ["event:old"]),
            lifecycle,
            subject="test lifecycle",
        )


def test_effect_graph_wire_projects_typed_lifecycle_graph() -> None:
    instrument = SourceInstrumentRef(
        instrument_id="2024/1",
        title="Amending Act",
        enacted="2024-01-01",
        effective="2024-02-01",
    )
    witness = SourceProvisionRef(
        instrument=instrument,
        path=("1", "2"),
        span_id="s-1",
        text_excerpt="Effect witness",
        rule_id="test.effect_graph_wire",
    )
    effect = EffectRef(
        effect_id="effect:2024/1:op-1",
        source_instrument=instrument,
        target_statute="1991/1",
        target_address=LegalAddress(path=(("section", "4 a"),), special=FacetKind.HEADING),
        projection_group_id="g:2024/1:op-1",
        source_provision=witness,
    )
    relation = EffectRelation(
        relation_id="relation:2024/1:op-1",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=effect,
        detail={"source_finding": "APPLY.META_REPEAL_EFFECT_RECORDED"},
    )
    temporal = TemporalEvent(
        event_id="temporal:relation:2024/1:op-1",
        kind="expire",
        scope=TemporalScope(
            target_statute="1991/1",
            exact_addresses=(
                LegalAddress(path=(("section", "4 a"),), special=FacetKind.HEADING),
            ),
        ),
        expires="2024-02-01",
        group_id="g:2024/1:op-1",
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:2024/1:op-1:repeal",
        kind="repeal_effect",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2024-02-01",
        temporal_event=temporal,
        executable=True,
        detail={"projection": "effect_relation_signal"},
    )

    payload = effect_graph_wire(
        source_effects=(effect,),
        effect_relations=(relation,),
        effect_lifecycle_events=(lifecycle,),
        detail_converter=lambda detail: {"converted": tuple(sorted(detail))},
    )

    assert payload == {
        "source_effects": (
            {
                "effect_id": "effect:2024/1:op-1",
                "source_instrument": {
                    "instrument_id": "2024/1",
                    "title": "Amending Act",
                    "enacted": "2024-01-01",
                    "effective": "2024-02-01",
                    "expires": "",
                },
                "target_statute": "1991/1",
                "target_address": {
                    "path": ({"kind": "section", "label": "4 a"},),
                    "special": "heading",
                },
                "projection_group_id": "g:2024/1:op-1",
                "source_provision": {
                    "instrument": {
                        "instrument_id": "2024/1",
                        "title": "Amending Act",
                        "enacted": "2024-01-01",
                        "effective": "2024-02-01",
                        "expires": "",
                    },
                    "path": ("1", "2"),
                    "span_id": "s-1",
                    "text_excerpt": "Effect witness",
                    "rule_id": "test.effect_graph_wire",
                    "witness_id": "2024/1:1/2",
                },
            },
        ),
        "effect_relations": (
            {
                "relation_id": "relation:2024/1:op-1",
                "kind": "repeals_effect",
                "source_provision": {
                    "instrument": {
                        "instrument_id": "2024/1",
                        "title": "Amending Act",
                        "enacted": "2024-01-01",
                        "effective": "2024-02-01",
                        "expires": "",
                    },
                    "path": ("1", "2"),
                    "span_id": "s-1",
                    "text_excerpt": "Effect witness",
                    "rule_id": "test.effect_graph_wire",
                    "witness_id": "2024/1:1/2",
                },
                "target_effect_id": "effect:2024/1:op-1",
                "target_instrument": None,
                "source_effect_id": "",
                "target_resolution": {
                    "kind": "target_effect_resolved",
                    "matched_effect_count": 1,
                    "non_executable_reason": "",
                },
                "detail": {"converted": ("source_finding",)},
            },
        ),
        "effect_lifecycle_events": (
            {
                "lifecycle_event_id": "lifecycle:2024/1:op-1:repeal",
                "kind": "repeal_effect",
                "source_provision": {
                    "instrument": {
                        "instrument_id": "2024/1",
                        "title": "Amending Act",
                        "enacted": "2024-01-01",
                        "effective": "2024-02-01",
                        "expires": "",
                    },
                    "path": ("1", "2"),
                    "span_id": "s-1",
                    "text_excerpt": "Effect witness",
                    "rule_id": "test.effect_graph_wire",
                    "witness_id": "2024/1:1/2",
                },
                "effect_id": "effect:2024/1:op-1",
                "relation_id": "relation:2024/1:op-1",
                "effective": "",
                "expires": "2024-02-01",
                "expiry_convention": "exclusive_cutoff",
                "temporal_event": {
                    "event_id": "temporal:relation:2024/1:op-1",
                    "kind": "expire",
                    "effective": "",
                    "expires": "2024-02-01",
                    "group_id": "g:2024/1:op-1",
                },
                "executable": True,
                "intended_lifecycle_kind": "",
                "intended_relation_kind": "",
                "detail": {"converted": ("projection",)},
            },
        ),
    }


def test_effect_graph_wire_requires_closed_graph() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:2024/1:op-1", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:2024/1:op-1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:2024/1:op-1:expiry",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2024-12-31",
    )

    with pytest.raises(ValueError, match="references missing target_effect"):
        effect_graph_wire(
            source_effects=(),
            effect_relations=(relation,),
            effect_lifecycle_events=(),
        )
    with pytest.raises(ValueError, match="references missing relation"):
        effect_graph_wire(
            source_effects=(effect,),
            effect_relations=(),
            effect_lifecycle_events=(lifecycle,),
        )


def test_unresolved_effect_lifecycle_event_cannot_emit_executable_projection() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    event = EffectLifecycleEvent(
        lifecycle_event_id="life:1",
        kind="unresolved_effect_target",
        source_provision=SourceProvisionRef(instrument=instrument),
        executable=False,
    )
    bundle = CanonicalBundle(effect_lifecycle_events=(event,))

    assert bundle.lifecycle_projected_temporal_events == ()


def test_unresolved_effect_lifecycle_event_cannot_smuggle_resolved_target() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument)
    target_effect = EffectRef(effect_id="effect:target", source_instrument=instrument)

    with pytest.raises(ValueError, match="cannot name effect"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:effect",
            kind="unresolved_effect_target",
            source_provision=witness,
            effect=target_effect,
            executable=False,
        )

    with pytest.raises(ValueError, match="relation cannot name target_effect"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:relation",
            kind="unresolved_effect_target",
            source_provision=witness,
            relation=EffectRelation(
                relation_id="relation:resolved",
                kind="modifies_effect",
                source_provision=witness,
                target_effect=target_effect,
            ),
            executable=False,
        )


def test_lifecycle_event_relation_requires_supported_kind_and_same_source_witness() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    other_witness = SourceProvisionRef(instrument=instrument, path=("2",))
    effect = EffectRef(effect_id="effect:target", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:resolved",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=effect,
    )
    unresolved_relation = EffectRelation(
        relation_id="relation:unresolved",
        kind="repeals_effect",
        source_provision=witness,
        target_instrument=instrument,
    )

    with pytest.raises(ValueError, match="relation is only supported"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:commence-relation",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            relation=relation,
            effective="2020-01-01",
        )

    with pytest.raises(ValueError, match="source_provision must match relation source_provision"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:unresolved-source-mismatch",
            kind="unresolved_effect_target",
            source_provision=other_witness,
            relation=unresolved_relation,
            executable=False,
        )


def test_resolved_effect_lifecycle_event_requires_target_effect() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")

    with pytest.raises(ValueError, match="resolved EffectLifecycleEvent requires effect"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:1",
            kind="change_effect_expiry",
            source_provision=SourceProvisionRef(instrument=instrument),
            executable=False,
        )


def test_executable_expiry_lifecycle_event_requires_resolved_date() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument)
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:1",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=effect,
    )

    with pytest.raises(
        ValueError,
        match="executable expiry/repeal EffectLifecycleEvent requires effective or expires date",
    ):
        EffectLifecycleEvent(
            lifecycle_event_id="life:1",
            kind="repeal_effect",
            source_provision=witness,
            effect=effect,
            relation=relation,
            executable=True,
        )


def test_effect_modifying_lifecycle_event_requires_relation() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument)
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)

    with pytest.raises(
        ValueError,
        match="effect-modifying EffectLifecycleEvent requires EffectRelation",
    ):
        EffectLifecycleEvent(
            lifecycle_event_id="life:1",
            kind="change_effect_commencement",
            source_provision=witness,
            effect=effect,
            effective="2020-01-01",
        )


def test_effect_modifying_lifecycle_event_relation_must_match_event() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    other_witness = SourceProvisionRef(instrument=instrument, path=("2",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)
    other_effect = EffectRef(effect_id="effect:2", source_instrument=instrument)
    matching_relation = EffectRelation(
        relation_id="relation:1",
        kind="changes_effect_commencement",
        source_provision=witness,
        target_effect=effect,
    )

    with pytest.raises(
        ValueError,
        match="relation kind must be 'changes_effect_commencement'",
    ):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-kind",
            kind="change_effect_commencement",
            source_provision=witness,
            effect=effect,
            relation=EffectRelation(
                relation_id="relation:wrong-kind",
                kind="extends_effect_expiry",
                source_provision=witness,
                target_effect=effect,
            ),
            effective="2020-01-01",
        )
    with pytest.raises(ValueError, match="relation target must match event effect"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-target",
            kind="change_effect_commencement",
            source_provision=witness,
            effect=effect,
            relation=EffectRelation(
                relation_id="relation:wrong-target",
                kind="changes_effect_commencement",
                source_provision=witness,
                target_effect=other_effect,
            ),
            effective="2020-01-01",
        )
    with pytest.raises(ValueError, match="source_provision must match relation source_provision"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-source",
            kind="change_effect_commencement",
            source_provision=other_witness,
            effect=effect,
            relation=matching_relation,
            effective="2020-01-01",
        )


def test_pending_amendment_effect_unresolved_blocks_strict_mode() -> None:
    finding = Finding(
        kind="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
        role="obligation",
        stage="process_muutoslaki",
        detail={"target_amendment_id": "2020/1"},
        source_statute="2021/2",
        blocking=True,
    )

    assert strict_fail_reasons_from_finding_ledger(
        StrictProfile(name="strict"),
        compiled_ops=(),
        canonical_ops=(),
        failures=(),
        findings=(finding,),
    ) == ["APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED"]


def test_unresolved_effect_lifecycle_event_blocks_strict_mode_without_finding() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    event = EffectLifecycleEvent(
        lifecycle_event_id="life:unresolved",
        kind="unresolved_effect_target",
        source_provision=SourceProvisionRef(instrument=instrument),
        executable=False,
    )

    assert strict_fail_reasons_from_finding_ledger(
        StrictProfile(name="strict"),
        compiled_ops=(),
        canonical_ops=(),
        failures=(),
        findings=(),
        effect_lifecycle_events=(event,),
    ) == ["APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED"]


def test_effect_lifecycle_event_lowers_to_temporal_event_semantics() -> None:
    instrument = SourceInstrumentRef(
        instrument_id="2020/1",
        title="Amending Act",
        enacted="2020-01-01",
        effective="2020-06-01",
    )
    witness = SourceProvisionRef(instrument=instrument, path=("2 §",), text_excerpt="Tulee voimaan 1.6.2020.")
    effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "1"),)),
        source_provision=witness,
    )
    event = EffectLifecycleEvent(
        lifecycle_event_id="life:2020/1:op-1:commence",
        kind="commence_effect",
        source_provision=witness,
        effect=effect,
        effective="2020-06-01",
    )
    bundle = CanonicalBundle(
        source_effects=(effect,),
        effect_lifecycle_events=(event,),
    )

    (temporal_event,) = bundle.lifecycle_projected_temporal_events
    assert temporal_event.kind == "commence"
    assert temporal_event.effective == "2020-06-01"
    assert temporal_event.scope.target_statute == "1999/1"
    assert temporal_event.scope.exact_addresses == (LegalAddress(path=(("section", "1"),)),)
    assert temporal_event.source is not None
    assert temporal_event.source.statute_id == "2020/1"


def test_canonical_bundle_executable_temporal_events_dedupes_lifecycle_projection() -> None:
    direct = TemporalEvent(
        event_id="life:2020/1:op-1:commence:temporal",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-06-01",
        source=OperationSource(statute_id="2020/1", effective="2020-06-01"),
    )
    instrument = SourceInstrumentRef(instrument_id="2020/1", effective="2020-06-01")
    witness = SourceProvisionRef(instrument=instrument, path=("2 §",))
    effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=instrument,
        target_statute="1999/1",
        source_provision=witness,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="life:2020/1:op-1:commence",
        kind="commence_effect",
        source_provision=witness,
        effect=effect,
        effective="2020-06-01",
        temporal_event=direct,
    )
    bundle = CanonicalBundle(
        temporal_events=(direct,),
        source_effects=(effect,),
        effect_lifecycle_events=(lifecycle,),
    )

    assert bundle.executable_temporal_events == (direct,)


def test_lifecycle_event_temporal_projection_must_match_lifecycle_semantics() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1", effective="2020-06-01")
    witness = SourceProvisionRef(instrument=instrument, path=("2 §",))
    effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "1"),)),
        source_provision=witness,
    )

    with pytest.raises(ValueError, match="temporal_event kind must match"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-kind",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            effective="2020-06-01",
            temporal_event=TemporalEvent(
                event_id="life:wrong-kind:temporal",
                kind="expire",
                scope=TemporalScope(
                    target_statute="1999/1",
                    exact_addresses=(LegalAddress(path=(("section", "1"),)),),
                ),
                expires="2020-06-01",
            ),
        )

    with pytest.raises(ValueError, match="effective date must match"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-effective",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            effective="2020-06-01",
            temporal_event=TemporalEvent(
                event_id="life:wrong-effective:temporal",
                kind="commence",
                scope=TemporalScope(
                    target_statute="1999/1",
                    exact_addresses=(LegalAddress(path=(("section", "1"),)),),
                ),
                effective="2020-07-01",
            ),
        )

    with pytest.raises(ValueError, match="exact address scope must match"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-scope",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            effective="2020-06-01",
            temporal_event=TemporalEvent(
                event_id="life:wrong-scope:temporal",
                kind="commence",
                scope=TemporalScope(
                    target_statute="1999/1",
                    exact_addresses=(LegalAddress(path=(("section", "2"),)),),
                ),
                effective="2020-06-01",
            ),
        )

    with pytest.raises(ValueError, match="non-executable EffectLifecycleEvent"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:non-executable-temporal",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            effective="2020-06-01",
            executable=False,
            temporal_event=TemporalEvent(
                event_id="life:non-executable-temporal:temporal",
                kind="commence",
                scope=TemporalScope(
                    target_statute="1999/1",
                    exact_addresses=(LegalAddress(path=(("section", "1"),)),),
                ),
                effective="2020-06-01",
            ),
        )


def test_lifecycle_expiry_temporal_projection_matches_inclusive_expiry_convention() -> None:
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("2 §",))
    effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "1"),)),
        source_provision=witness,
    )
    relation = EffectRelation(
        relation_id="relation:2020/1:op-1:expiry",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )

    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="life:expiry",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2020-12-31",
        expiry_convention="inclusive_valid_until",
        temporal_event=TemporalEvent(
            event_id="life:expiry:temporal",
            kind="expire",
            scope=TemporalScope(
                target_statute="1999/1",
                exact_addresses=(LegalAddress(path=(("section", "1"),)),),
            ),
            expires="2021-01-01",
        ),
    )

    assert lifecycle.temporal_event is not None
    assert lifecycle.temporal_event.expires == "2021-01-01"

    with pytest.raises(ValueError, match="expires date must match"):
        EffectLifecycleEvent(
            lifecycle_event_id="life:wrong-expiry",
            kind="change_effect_expiry",
            source_provision=witness,
            effect=effect,
            relation=relation,
            expires="2020-12-31",
            expiry_convention="inclusive_valid_until",
            temporal_event=TemporalEvent(
                event_id="life:wrong-expiry:temporal",
                kind="expire",
                scope=TemporalScope(
                    target_statute="1999/1",
                    exact_addresses=(LegalAddress(path=(("section", "1"),)),),
                ),
                expires="2020-12-31",
            ),
        )


def test_canonical_bundle_rejects_conflicting_executable_temporal_event_ids() -> None:
    direct = TemporalEvent(
        event_id="life:2020/1:op-1:commence:temporal",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-06-01",
        source=OperationSource(statute_id="2020/1", effective="2020-06-01"),
    )
    instrument = SourceInstrumentRef(instrument_id="2020/1", effective="2020-06-01")
    witness = SourceProvisionRef(instrument=instrument, path=("2 §",))
    effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=instrument,
        target_statute="1999/1",
        source_provision=witness,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="life:2020/1:op-1:commence",
        kind="commence_effect",
        source_provision=witness,
        effect=effect,
        effective="2020-06-01",
    )
    with pytest.raises(ValueError, match="conflicting duplicate event_id"):
        CanonicalBundle(
            temporal_events=(direct,),
            source_effects=(effect,),
            effect_lifecycle_events=(lifecycle,),
        )


def test_lifecycle_event_lowering_rejects_untyped_inputs() -> None:
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        lower_lifecycle_events_to_temporal_events(cast(Any, ("life:1",)))


def test_compiled_op_provenance_tags_freeze_and_validate_tag_sets() -> None:
    tags = CompiledOpProvenanceTags(extraction_tags=cast(Any, ["xml", "feed"]))

    assert tags.extraction_tags == frozenset({"xml", "feed"})
    with pytest.raises(ValueError, match="scope_tags"):
        CompiledOpProvenanceTags(scope_tags=cast(Any, ["ok", 1]))
    with pytest.raises(ValueError, match="not a string"):
        CompiledOpProvenanceTags(scope_sources=cast(Any, "explicit_chunk"))


def test_compiled_op_evidence_row_requires_typed_carriers_and_drives_tag_sets() -> None:
    evidence = CompiledOpEvidenceRow(
        source_statute=" 2024/1 ",
        provenance_tags=CompiledOpProvenanceTags(
            extraction_tags=frozenset({"extraction_fallback_heuristic"}),
            scope_sources=frozenset({"explicit_chunk"}),
        ),
        scope_witness=CompiledOpScopeWitness(
            kind="LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED",
            source="explicit_chunk",
            confidence="explicit",
        ),
    )

    assert evidence.source_statute == "2024/1"
    assert _compiled_op_provenance_tag_sets((evidence,)).extraction_tags == frozenset(
        {"extraction_fallback_heuristic"}
    )
    with pytest.raises(ValueError, match="provenance_tags"):
        CompiledOpEvidenceRow(provenance_tags=cast(Any, object()))
    with pytest.raises(ValueError, match="scope_witness"):
        CompiledOpEvidenceRow(scope_witness=cast(Any, object()))


def test_compiled_op_scope_witness_rejects_empty_or_untyped_fields() -> None:
    with pytest.raises(ValueError, match="kind"):
        CompiledOpScopeWitness(kind="", source="explicit_chunk", confidence="explicit")
    with pytest.raises(ValueError, match="used_legacy_tag_fallback"):
        CompiledOpScopeWitness(
            kind="LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED",
            source="explicit_chunk",
            confidence="explicit",
            used_legacy_tag_fallback=cast(Any, "yes"),
        )


def test_compiled_op_scope_witness_rejects_unrecognized_scope_source() -> None:
    """Unrecognized scope-source values are not classified into a witness."""
    assert _compiled_op_scope_witness(
        {"scope_source": "mystery_source", "scope_confidence": "inferred"}
    ) is None


def test_admissible_binding_coverage_rejects_count_contradictions() -> None:
    assert (
        AdmissibleBindingCoverage(
            slot_id=1,
            amendment_id="",
            candidate_count=1,
            admissibility="single",
        ).amendment_id
        == ""
    )
    with pytest.raises(ValueError, match="single admissibility"):
        AdmissibleBindingCoverage(
            slot_id=1,
            amendment_id="2024/100",
            candidate_count=2,
            admissibility="single",
        )
    with pytest.raises(ValueError, match="ambiguous admissibility"):
        AdmissibleBindingCoverage(
            slot_id=1,
            amendment_id="2024/100",
            candidate_count=1,
            admissibility="ambiguous",
        )


# ---------------------------------------------------------------------------
# _validate_bundle_purity (standalone function)
# ---------------------------------------------------------------------------


class TestValidateBundlePurityFunction:
    def test_empty_ops_returns_no_violations(self):
        assert _validate_bundle_purity(()) == []

    def test_legal_operation_returns_no_violations(self):
        op = _legal_op()
        assert _validate_bundle_purity((op,)) == []

    def test_multiple_legal_operations_no_violations(self):
        ops = (_legal_op("op-1", "1"), _legal_op("op-2", "2"))
        assert _validate_bundle_purity(ops) == []

    def test_frontend_local_type_returns_violation(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),))  # intentional type violation
        assert len(violations) == 1
        assert "_FrontendLocalOp" in violations[0]
        assert "structural_ops[0]" in violations[0]

    def test_mixed_ops_reports_only_non_legal_operations(self):
        op = _legal_op()
        bad = _FrontendLocalOp()
        violations = _validate_bundle_purity((op, bad))
        assert len(violations) == 1
        assert "structural_ops[1]" in violations[0]

    def test_caller_name_appears_in_violation_message(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),), caller="TestCaller")
        assert "TestCaller" in violations[0]

    def test_violation_message_mentions_lowering_requirement(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),))
        assert "lowered" in violations[0]


# ---------------------------------------------------------------------------
# CanonicalBundle construction -- purity check in __post_init__
# ---------------------------------------------------------------------------


class TestCanonicalBundleConstructionPurity:
    def test_empty_bundle_constructs_cleanly(self):
        bundle = CanonicalBundle()
        assert bundle.structural_ops == ()

    def test_legal_ops_only_constructs_cleanly(self):
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.structural_ops == (op,)

    def test_frontend_local_op_raises_type_error(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="non-LegalOperation"):
            CanonicalBundle(structural_ops=_runtime_structural_ops(bad))

    def test_type_error_mentions_type_name(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="_FrontendLocalOp"):
            CanonicalBundle(structural_ops=_runtime_structural_ops(bad))

    def test_type_error_mentions_lowering_requirement(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="lowered"):
            CanonicalBundle(structural_ops=_runtime_structural_ops(bad))

    def test_mixed_ops_raises_type_error(self):
        op = _legal_op()
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="non-LegalOperation"):
            CanonicalBundle(structural_ops=_runtime_structural_ops(op, bad))


class TestCanonicalEffectContracts:
    def test_canonical_effect_rejects_unknown_family(self) -> None:
        with pytest.raises(TypeError, match="family"):
            CanonicalEffect(
                effect_id="effect:1",
                family=cast(Any, "python_order"),
                action=cast(Any, "replace"),
                target=LegalAddress(path=(("section", "1"),)),
            )

    def test_canonical_effect_rejects_untyped_target(self) -> None:
        with pytest.raises(TypeError, match="target"):
            CanonicalEffect(
                effect_id="effect:1",
                family="text",
                action="text_patch",
                target=cast(Any, "section:1"),
            )


# ---------------------------------------------------------------------------
# CanonicalBundle.validate_purity() method
# ---------------------------------------------------------------------------


class TestCanonicalBundleValidatePurity:
    def test_pure_bundle_returns_empty_list(self):
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.validate_purity() == []

    def test_empty_bundle_returns_empty_list(self):
        bundle = CanonicalBundle()
        assert bundle.validate_purity() == []

    def test_impure_bundle_returns_violations(self):
        bad = _FrontendLocalOp()
        bundle = _impure_bundle(structural_ops=(bad,))
        violations = bundle.validate_purity()
        assert len(violations) == 1
        assert "_FrontendLocalOp" in violations[0]

    def test_validate_purity_is_idempotent(self):
        """Calling validate_purity() twice returns the same result."""
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.validate_purity() == bundle.validate_purity()


class TestCanonicalBundleLineage:
    def test_lineage_is_not_derived_from_structural_ops(self) -> None:
        op = LegalOperation(
            op_id="renumber-op",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            destination=LegalAddress(path=(("section", "1a"),)),
        )
        bundle = CanonicalBundle(structural_ops=(op,))

        assert bundle.migration_events == ()

    def test_provision_lineage_uses_bundle_migration_events(self) -> None:
        old_addr = LegalAddress(path=(("section", "1"),))
        new_addr = LegalAddress(path=(("section", "1a"),))
        migration_event = MigrationEvent(
            event_id="mig:bundle:1",
            kind="renumber",
            from_address=old_addr,
            to_address=new_addr,
            effective="2020-01-01",
        )
        version = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="1a"),
        )
        bundle = CanonicalBundle(migration_events=(migration_event,))
        timelines = {
            new_addr: ProvisionTimeline(
                address=new_addr,
                versions=[version],
            )
        }

        assert bundle.provision_lineage(timelines, old_addr) == [version]

    def test_materialize_pit_uses_bundle_migration_events(self) -> None:
        old_addr = LegalAddress(path=(("section", "1"),))
        new_addr = LegalAddress(path=(("section", "1a"),))
        migration_event = MigrationEvent(
            event_id="mig:bundle:materialize:1",
            kind="renumber",
            from_address=old_addr,
            to_address=new_addr,
            effective="2020-01-01",
        )
        version = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="migrated"),
        )
        bundle = CanonicalBundle(migration_events=(migration_event,))
        timelines = {
            old_addr: ProvisionTimeline(
                address=old_addr,
                versions=[version],
            )
        }
        base = IRStatute(
            statute_id="test/bundle-materialize",
            title="Bundle materialize",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),),
            ),
        )

        pit = bundle.materialize_pit(timelines, "2025-01-01", base=base)

        assert [child.label for child in pit.body.children] == ["1a"]
        assert pit.body.children[0].text == "migrated"


class TestCanonicalBundleTemporalSummaries:
    def test_temporal_event_source_count_tracks_provenance_sources(self) -> None:
        bundle = CanonicalBundle(
            temporal_events=(
                TemporalEvent(event_id="temp:1", kind="commence", scope=TemporalScope(), source=OperationSource(statute_id="2024/1", enacted="2024-01-01")),
                TemporalEvent(event_id="temp:2", kind="expire", scope=TemporalScope()),
            ),
        )

        assert bundle.temporal_events_with_source == 1

    def test_temporal_summaries_include_lifecycle_projected_events(self) -> None:
        instrument = SourceInstrumentRef(instrument_id="2020/1")
        witness = SourceProvisionRef(
            instrument=instrument,
            path=("2 §",),
            text_excerpt="Tulee voimaan 1.6.2020.",
        )
        effect = EffectRef(
            effect_id="effect:2020/1:op-1",
            source_instrument=instrument,
            target_statute="1999/1",
            target_address=LegalAddress(path=(("section", "1"),)),
            source_provision=witness,
        )
        lifecycle = EffectLifecycleEvent(
            lifecycle_event_id="life:2020/1:commence",
            kind="commence_effect",
            source_provision=witness,
            effect=effect,
            effective="2020-06-01",
        )
        bundle = CanonicalBundle(
            source_effects=(effect,),
            effect_lifecycle_events=(lifecycle,),
        )

        assert bundle.temporal_event_kinds == ("commence",)
        assert bundle.temporal_events_with_activation_rules == 1
        assert bundle.temporal_events_with_source == 1
        assert bundle.temporal_event_activation_rule_kinds == ("fixed_date",)


class TestCompileResultTargetScopeNormalization:
    def test_normalize_target_unit_kind_prefers_neutral_vocabulary(self) -> None:
        assert normalize_target_unit_kind("chapter") == "chapter"
        assert normalize_target_unit_kind("section") == "section"
        assert normalize_target_unit_kind("L") == ""
        assert normalize_target_unit_kind("P") == ""

    def test_compiled_op_matches_section_does_not_treat_chapter_as_universal(self) -> None:
        assert not _compiled_op_matches_section(
            {"target_unit_kind": "chapter", "target_chapter": "3"},
            "12",
        )

    def test_compiled_op_matches_section_rejects_only_target_kind(self) -> None:
        assert not _compiled_op_matches_section({"target_kind": "L"}, "12")

    def test_compiled_op_matches_section_uses_top_level_neutral_scope(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
            },
            "chapter:3/section:12",
        )

    def test_compiled_op_matches_section_prefers_top_level_neutral_scope_over_nested_scope_payload(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
                "target": {
                    "container": "section",
                    "section": "12",
                },
            },
            "chapter:3/section:12",
        )

    def test_compiled_op_matches_section_prefers_top_level_neutral_scope_over_target_kind(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
                "target_kind": "P",
            },
            "chapter:3/section:12",
        )

    def test_operation_matches_section_rejects_broad_scope_addresses(self) -> None:
        op = LegalOperation(
            op_id="op-schedule",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"),)),
        )
        assert not _operation_matches_section(op, "12")

    def test_operation_matches_section_uses_broad_scope_addresses(self) -> None:
        op = LegalOperation(
            op_id="op-chapter",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "7"),)),
        )
        assert _operation_matches_section(op, "chapter:7/section:12")

    def test_compiled_op_source_statute_prefers_operation_source(self) -> None:
        assert _compiled_op_source_statute(
            {"source": OperationSource(statute_id="2024/5")}
        ) == "2024/5"

    def test_compiled_op_source_statute_ignores_unknown_source_object(self) -> None:
        assert (
            _compiled_op_source_statute({"source": SimpleNamespace(statute_id="2024/5")})
            == ""
        )

    def test_source_adjudication_shape_remains_constructible(self) -> None:
        adjudication = SourceAdjudication(statute_id="2024/1", replay_mode="strict")
        assert adjudication.statute_id == "2024/1"
        assert adjudication.replay_mode == "strict"

    def test_compile_adjudication_shape_remains_compat_bridge(self) -> None:
        adjudication = CompileAdjudication(
            kind="replay_target_not_found",
            message="target missing",
            source_statute="2024/1",
            blocking=True,
            phase="replay",
            op_id="op-1",
        )
        assert adjudication.kind == "replay_target_not_found"
        assert adjudication.source_statute == "2024/1"


class TestCompileResultPathologyCarriers:
    def test_source_pathology_carries_neutral_target_unit_kind(self) -> None:
        pathology = SourcePathology(
            code="test",
            message="test",
            source_statute="2024/1",
            target_unit_kind="chapter",
        )
        assert pathology.target_unit_kind == "chapter"

    def test_source_pathology_rejects_implicit_structural_scope(self) -> None:
        with pytest.raises(ValueError, match="structural detail requires explicit neutral"):
            SourcePathology(
                code="test",
                message="test",
                detail={"target_section": "3"},
            )

    def test_source_pathology_supports_scope_less_non_structural_construction(self) -> None:
        pathology = SourcePathology(
            code="EMPTY_OPERATIVE_BODY",
            message="test",
        )
        assert pathology.target_unit_kind == ""

    def test_source_pathology_freezes_detail_recursively(self) -> None:
        source_detail: dict[str, Any] = {"nested": {"items": ["a"]}}

        pathology = SourcePathology(
            code="EMPTY_OPERATIVE_BODY",
            message="test",
            detail=source_detail,
        )
        source_detail["nested"]["items"].append("mutated")

        assert pathology.detail == {"nested": {"items": ("a",)}}
        frozen_detail = cast(Any, pathology.detail)
        with pytest.raises(TypeError, match="immutable"):
            frozen_detail["extra"] = "blocked"

    def test_source_pathology_from_internal_detail_requires_neutral_scope(self) -> None:
        with pytest.raises(ValueError, match="requires explicit neutral target_unit_kind"):
            SourcePathology.from_internal_detail(
                source_statute="2024/1",
                detail={
                    "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                    "message": "test",
                    "target_label": "3",
                },
            )

    def test_source_pathology_from_internal_detail_ignores_target_kind_when_scope_is_neutral(
        self,
    ) -> None:
        pathology = SourcePathology.from_internal_detail(
            source_statute="2024/1",
            detail={
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "test",
                "target_kind": "L",
                "target_unit_kind": "chapter",
                "target_label": "3",
            },
        )
        assert pathology.target_unit_kind == "chapter"
        assert pathology.target_label == "3"
        assert pathology.detail == {"target_kind": "L"}

    def test_source_pathology_from_internal_detail_keeps_explicit_non_structural_case(self) -> None:
        pathology = SourcePathology.from_internal_detail(
            source_statute="2024/1",
            detail={
                "code": "EMPTY_OPERATIVE_BODY",
                "message": "test",
                "target_label": "2024/1",
            },
        )
        assert pathology.target_unit_kind == ""

    def test_compile_failure_carries_neutral_target_unit_kind(self) -> None:
        failure = CompileFailure(
            source_statute="2024/1",
            description="test",
            reason="oops",
            target_section="3",
            target_unit_kind="chapter",
        )
        assert failure.target_unit_kind == "chapter"
        assert failure.scope_detail()["target_unit_kind"] == "chapter"

    def test_compile_failure_from_scope_preserves_reason_code(self) -> None:
        failure = CompileFailure.from_scope(
            source_statute="2024/1",
            description="test",
            reason="oops",
            target_section="3",
            target_unit_kind="section",
            reason_code="ELAB.REJECTED_LANGUAGE_VARIANT_ONLY",
        )

        assert failure.reason_code == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"
        assert failure.as_detail()["reason_code"] == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"


class TestCompileVerdictRail:
    def test_compile_verdict_rejects_clean_status_with_barriers(self) -> None:
        with pytest.raises(ValueError, match="strict_clean"):
            CompileVerdict(
                mode="strict",
                profile="test",
                verdict_status="strict_clean",
                barrier_codes=("APPLY.TREE_INVARIANT_VIOLATION",),
            )

    def test_compile_verdict_rejects_unknown_status(self) -> None:
        with pytest.raises(ValueError, match="status"):
            CompileVerdict(
                mode="strict",
                profile="test",
                verdict_status=cast(Any, "last_write_wins"),
            )

    def test_section_strict_verdict_rejects_clean_status_with_barriers(self) -> None:
        with pytest.raises(ValueError, match="strict_clean"):
            SectionStrictVerdict(
                section_label="1",
                amendment_id="2024/1",
                verdict_status="strict_clean",
                barrier_codes=("APPLY.TREE_INVARIANT_VIOLATION",),
            )

    def test_compile_verdict_barrier_codes_authoritatively_override_runtime_projection(self) -> None:
        """Verdict barrier rail is authoritative; runtime findings do not carry barrier kinds."""
        verdict = CompileVerdict(
            mode="strict",
            profile="test",
            verdict_status="strict_blocked_by_recovery",
            barrier_codes=("APPLY.TREE_INVARIANT_VIOLATION",),
        )
        violation = Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            detail={"section": "1"},
            blocking=True,
        )

        reasons = strict_fail_reasons_from_findings_and_verdict((violation,), verdict=verdict)

        assert reasons == (
            "APPLY.TREE_INVARIANT_VIOLATION",
            "RUNTIME.VIOLATION",
        )


class TestStrictFailReasonsFromFindingLedger:
    def test_strict_fail_reasons_detects_text_substitution_from_structural_enum(self) -> None:
        profile = StrictProfile(name="test")
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(
                LegalOperation(
                    op_id="op",
                    sequence=1,
                    action=StructuralAction.TEXT_REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                ),
            ),
            failures=(),
            findings=(),
        )

        assert reasons == ["APPLY.WORD_SUBSTITUTION"]

    def test_strict_fail_reasons_is_not_derived_from_source_effective_dates(self) -> None:
        profile = StrictProfile(name="test")
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(
                LegalOperation(
                    op_id="op",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                    source=OperationSource(statute_id="2024/1", enacted="2024-01-01"),
                ),
            ),
            failures=(),
            findings=(),
        )

        assert "TIME.MISSING_EFFECTIVE_DATE" not in reasons

    def test_strict_fail_reasons_respects_corrigendum_policy(self) -> None:
        profile = StrictProfile(name="test", allows_source_correction_rules=True)
        findings = (
            Finding(
                kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
                role="obligation",
                stage="apply",
                detail={},
                blocking=True,
            ),
        )
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(),
            failures=(),
            findings=findings,
        )

        assert "APPLY.SOURCE_CORRECTED_BY_PATCH" not in reasons

    def test_strict_fail_reasons_derives_runtime_violation_generically(self) -> None:
        profile = StrictProfile(name="test")
        findings = (
            Finding(
                kind="RUNTIME.VIOLATION",
                role=VIOLATION_ROLE,
                stage="replay",
                detail={},
                blocking=True,
            ),
        )

        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(),
            failures=(),
            findings=findings,
        )

        assert reasons == ["RUNTIME.VIOLATION"]


class TestStrictFailReasonsFromFindingsAndVerdict:
    def test_strict_fail_reasons_from_findings_and_verdict_excludes_barrier_registry_codes(self) -> None:
        projected_barrier_row = SimpleNamespace(
            kind="APPLY.TREE_INVARIANT_VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={},
            blocking=True,
        )
        runtime_violation = Finding(
            kind="RUNTIME.VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={},
            blocking=True,
        )
        findings = cast(tuple[Finding, ...], (projected_barrier_row, runtime_violation))

        reasons = strict_fail_reasons_from_findings_and_verdict(findings)

        assert reasons == ("RUNTIME.VIOLATION",)

    def test_strict_fail_reasons_from_findings_and_verdict_projects_apply_boundary_barrier_codes(
        self,
    ) -> None:
        runtime_violation = Finding(
            kind="RUNTIME.VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={"barrier_code": "REPLAY_SKIPPED_OP_MUTATED_TREE"},
            blocking=True,
        )

        reasons = strict_fail_reasons_from_findings_and_verdict((runtime_violation,))

        assert reasons == ("REPLAY_SKIPPED_OP_MUTATED_TREE",)
