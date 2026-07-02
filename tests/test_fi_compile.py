from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from lxml import etree
from types import SimpleNamespace
import warnings
from typing import Any, Literal, cast

import pytest

from lawvm.core.compile_result import (
    CompileFailure,
    StrictProfile,
    barrier_family_from_registry,
    compute_verdict_from_registry,
    strict_fail_reasons_from_finding_ledger,
)
from lawvm.core.compile_views import (
    projection_rows_from_findings,
    source_pathology_rows_from_findings,
)
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    SourceInstrumentRef,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
    StructuralAction,
)
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.phase_result import Finding
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.finland.strict_profile import default_finland_strict_profile
from lawvm.core.observation_registry import (
    FINDING_REGISTRY,
    get_finding_spec,
    finding_codes_by_role,
    strict_fail_codes_by_enforcement,
    strict_fail_codes_by_family,
)
from lawvm.core.semantic_types import IRNodeKind, TextPatchKindEnum
from lawvm.finland.ops import OpType, AmendmentOp, FailedOp, classify_legal_operation_conversion_skip
from lawvm.finland.op_provenance import (
    RecognizerId,
    has_recognizer,
    serialized_provenance_from_bags,
)
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.replay_products import ReplayProducts
from lawvm.tools.section_keys import extract_ir_sections
from lawvm.finland.statute import ReplayResult, ReplayState, StatuteContext
from tests.corpus_pin_helpers import pinned_replay, replay_xml_for_test
from lawvm.tools.inspect_amendment import build_amendment_bundle


@dataclass(frozen=True, slots=True)
class _ReplayCompileInputs:
    parent_id: str
    replay_result: ReplayResult
    compiled_ops: tuple[dict[str, object], ...]
    replay_meta: dict[str, object]
    canonical_ops: tuple[LegalOperation, ...]
    failed_ops: tuple[Any, ...]


def compile_fi_facade(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.compile import compile_fi_facade as _real_compile_fi_facade

    return _real_compile_fi_facade(*args, **kwargs)


def compile_fi_facade_from_replay(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.compile import compile_fi_facade_from_replay as _real_compile_fi_facade_from_replay

    return _real_compile_fi_facade_from_replay(*args, **kwargs)


def get_corpus_store() -> Any:
    from lawvm.finland.corpus import get_corpus_store as _real_get_corpus_store

    return _real_get_corpus_store()


def compile_amendment_ops(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.compile_amendment import compile_amendment_ops as _real_compile_amendment_ops
    from lawvm.finland.source_model import AmendmentSourceModel

    if "muutos_tree" in kwargs:
        tree = kwargs.pop("muutos_tree")
        kwargs["source_model"] = AmendmentSourceModel.from_tree(
            tree,
            source_ref=str(kwargs.get("source_ref", "") or ""),
        )
    elif len(args) >= 3 and not isinstance(args[2], AmendmentSourceModel):
        patched_args = list(args)
        patched_args[2] = AmendmentSourceModel.from_tree(
            args[2],
            source_ref=str(kwargs.get("source_ref", "") or ""),
        )
        args = tuple(patched_args)

    return _real_compile_amendment_ops(*args, **kwargs)


def test_normalize_and_compile_ops_promotes_payload_fixed_term_section_to_temporary() -> None:
    xml = b"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act name="amendment">
        <docTitle>Testiasetus 8 a \xc2\xa7:n muuttamisesta</docTitle>
        <body>
          <hcontainer name="statuteProvisionsWrapper">
            <section>
              <num>8 a \xc2\xa7</num>
              <heading>Raportti</heading>
              <subsection>
                <content>
                  <p>Ministeri\xc3\xb6 laatii raportin.</p>
                </content>
              </subsection>
              <subsection>
                <content>
                  <p>T\xc3\xa4m\xc3\xa4 asetus tulee voimaan 1 p\xc3\xa4iv\xc3\xa4n\xc3\xa4 toukokuuta 2019 ja on voimassa 30 p\xc3\xa4iv\xc3\xa4\xc3\xa4n huhtikuuta 2025 asti.</p>
                </content>
              </subsection>
            </section>
          </hcontainer>
          <hcontainer name="entryIntoForce">
            <content>
              <p>T\xc3\xa4m\xc3\xa4 asetus tulee voimaan 1 p\xc3\xa4iv\xc3\xa4n\xc3\xa4 huhtikuuta 2023.</p>
            </content>
          </hcontainer>
        </body>
      </act>
    </akomaNtoso>"""
    tree = etree.fromstring(xml)
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="8a", children=()),),
        )
    )
    phase = normalize_and_compile_ops(
        "muutetaan testiasetuksen (834/2014) 8 a § seuraavasti:",
        tree,
        master=master,
        amendment_id="2023/197",
        source_title="Testiasetus 8 a §:n muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2014/834",
        strict_profile=None,
    )

    assert len(phase.output) == 1
    op = phase.output[0]
    assert op.target_cols.target_section == "8a"
    assert op.is_temporary is True
    assert op.lo is not None
    assert op.lo.source is not None
    assert op.lo.source.effective == "2023-04-01"
    assert op.lo.source.expires == "2025-05-01"
    assert [(event.kind, event.expires) for event in phase.temporal_events] == [
        ("commence", ""),
        ("expire", "2025-05-01"),
    ]
    assert {event.scope.target_statute for event in phase.temporal_events} == {"2014/834"}


def test_normalize_and_compile_ops_promotes_whole_entry_fixed_term_insert_to_temporary() -> None:
    xml = b"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act name="amendment">
        <body>
          <hcontainer name="statuteProvisionsWrapper">
            <section>
              <num>8 a \xc2\xa7</num>
              <heading>Raportti</heading>
              <subsection>
                <content>
                  <p>Ministeri\xc3\xb6 laatii raportin.</p>
                </content>
              </subsection>
            </section>
          </hcontainer>
          <hcontainer name="entryIntoForce">
            <content>
              <p>T\xc3\xa4m\xc3\xa4 asetus tulee voimaan 1 p\xc3\xa4iv\xc3\xa4n\xc3\xa4 toukokuuta 2019 ja on voimassa 30 p\xc3\xa4iv\xc3\xa4\xc3\xa4n huhtikuuta 2021 asti.</p>
            </content>
          </hcontainer>
        </body>
      </act>
    </akomaNtoso>"""
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="8", children=()),),
        )
    )

    phase = normalize_and_compile_ops(
        "lisätään testiasetukseen uusi 8 a § seuraavasti:",
        etree.fromstring(xml),
        master=master,
        amendment_id="2019/154",
        source_title="Testiasetus",
        used_preamble_body_fallback=False,
        parent_id="2014/834",
        strict_profile=None,
    )

    assert len(phase.output) == 1
    op = phase.output[0]
    assert op.op_type is OpType.INSERT
    assert op.is_temporary is True
    assert op.lo is not None
    assert op.lo.source is not None
    assert op.lo.source.expires == "2021-05-01"
    assert [(event.kind, event.scope.target_statute, event.expires) for event in phase.temporal_events] == [
        ("commence", "2014/834", ""),
        ("expire", "2014/834", "2021-05-01"),
    ]


def test_legal_operation_descendant_only_target_is_not_coerced_to_section() -> None:
    lo = LegalOperation(
        op_id="descendant-only",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("subsection", "1"),)),
    )

    skip = classify_legal_operation_conversion_skip(lo)

    assert skip is not None
    assert skip.reason_code == "ELAB.UNSUPPORTED_DESCENDANT_ONLY_TARGET"
    assert AmendmentOp.from_lo(lo, 0) == []


def test_amendment_op_with_lo_derives_target_unit_without_section_default() -> None:
    lo = LegalOperation(
        op_id="part-scoped-section",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "II"), ("chapter", "2"), ("section", "5"))),
    )

    op = AmendmentOp(op_id="part-scoped-section", op_type=OpType.RENUMBER, lo=lo)

    assert op.target_cols.target_unit_kind == "section"
    assert op.target_cols.target_part == "II"
    assert op.target_cols.target_chapter == "2"
    assert op.target_cols.target_section == "5"


def test_amendment_op_with_unsupported_lo_target_does_not_default_to_section() -> None:
    lo = LegalOperation(
        op_id="descendant-only",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("subsection", "1"),)),
    )

    with pytest.raises(ValueError, match="no Finland-supported primary unit"):
        AmendmentOp(op_id="descendant-only", op_type=OpType.REPEAL, lo=lo)


def get_johtolause(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.metadata import get_johtolause as _real_get_johtolause

    return _real_get_johtolause(*args, **kwargs)


def normalize_and_compile_ops(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.frontend_compile import normalize_and_compile_ops as _real_normalize_and_compile_ops

    if "mid" in kwargs and "amendment_id" not in kwargs:
        kwargs["amendment_id"] = kwargs.pop("mid")
    return _real_normalize_and_compile_ops(*args, **kwargs)


def replay_xml(*args: Any, **kwargs: Any) -> Any:
    from tests.corpus_pin_helpers import replay_xml_for_test

    return replay_xml_for_test(*args, **kwargs)


def _compile_artifacts_from_replay(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland._compile import _compile_artifacts_from_replay as _real_compile_artifacts_from_replay

    return _real_compile_artifacts_from_replay(*args, **kwargs)


def _failed_op_to_compile_failure(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland._compile import _failed_op_to_compile_failure as _real_failed_op_to_compile_failure

    return _real_failed_op_to_compile_failure(*args, **kwargs)

def _strict_barrier_codes() -> tuple[str, ...]:
    return tuple(sorted(finding_codes_by_role("barrier")))


def _expected_barrier_family_from_registry(code: str) -> str:
    spec = FINDING_REGISTRY[code]
    if spec.family == "violation":
        return "invariant"
    if spec.family == "ambiguity":
        return "temporal"
    if spec.family == "source_pathology":
        return "source"
    if spec.phase == "parse":
        return "extraction"
    if code == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION":
        return "resolution"
    if code == "APPLY.WORD_SUBSTITUTION":
        return "text_level"
    return "recovery"


def _runtime_violation(
    barrier_code: str,
    *,
    stage: str,
    message: str,
    source_statute: str = "",
    detail: dict[str, object] | None = None,
    blocking: bool = True,
) -> Finding:
    payload: dict[str, object] = {"message": message, **(detail or {})}
    payload["barrier_code"] = barrier_code
    return Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage=stage,
        detail=payload,
        source_statute=source_statute,
        blocking=blocking,
    )


def _projection_rows(facade: Any) -> tuple[dict[str, object], ...]:
    return projection_rows_from_findings(getattr(facade, "finding_ledger", ()) or ())


def _source_pathology_rows(facade: Any) -> tuple[dict[str, object], ...]:
    return source_pathology_rows_from_findings(getattr(facade, "finding_ledger", ()) or ())


def _replay_result_stub(
    *,
    temporal_events: tuple[TemporalEvent, ...] = (),
    migration_events: tuple[object, ...] = (),
    findings: tuple[Finding, ...] = (),
    source_adjudication: Any = None,
    source_effects: tuple[EffectRef, ...] = (),
    effect_relations: tuple[EffectRelation, ...] = (),
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...] = (),
) -> ReplayResult:
    body = IRNode(kind=IRNodeKind.BODY)
    ctx = StatuteContext(
        id="2009/953",
        title="Test",
        base_ir=body,
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        temporal_events=temporal_events,
        migration_events=cast(tuple[Any, ...], migration_events),
        source_adjudication=source_adjudication,
        source_effects=source_effects,
        effect_relations=effect_relations,
        effect_lifecycle_events=effect_lifecycle_events,
    )
    return ReplayResult(
        ctx=ctx,
        products=products,
        findings=findings,
    )


def _compile_facade_with_replay(
    parent_id: str,
    *,
    replay_mode: Literal["official_consolidation", "legal_pit"] = "legal_pit",
    compile_mode: str = "strict",
    strict_profile: StrictProfile | None = None,
) -> tuple[ReplayResult, Any]:
    compiled_ops: list[dict[str, object]] = []
    replay_meta: dict[str, object] = {}
    canonical_ops: list[LegalOperation] = []
    failed_ops: list[Any] = []
    replay_result = replay_xml(
        parent_id,
        mode=replay_mode,
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
        lo_ops_out=canonical_ops,
        failed_ops_out=failed_ops,
        strict_profile=None,
    )
    facade = compile_fi_facade_from_replay(
        parent_id=parent_id,
        replay_result=replay_result,
        replay_mode=cast(Any, replay_mode),
        compile_mode=cast(Any, compile_mode),
        strict_profile=strict_profile,
        compiled_ops=compiled_ops,
        replay_meta=replay_meta,
        canonical_ops=canonical_ops,
        failed_ops=failed_ops,
    )
    return replay_result, facade


@pytest.fixture(scope="module")
def replay_compile_inputs_2002_1090_legal_pit() -> _ReplayCompileInputs:
    compiled_ops: list[dict[str, object]] = []
    replay_meta: dict[str, object] = {}
    canonical_ops: list[LegalOperation] = []
    failed_ops: list[Any] = []
    replay_result = replay_xml(
        "2002/1090",
        mode="legal_pit",
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
        lo_ops_out=canonical_ops,
        failed_ops_out=failed_ops,
        strict_profile=None,
        build_full_products=False,
    )
    return _ReplayCompileInputs(
        parent_id="2002/1090",
        replay_result=replay_result,
        compiled_ops=tuple(compiled_ops),
        replay_meta=dict(replay_meta),
        canonical_ops=tuple(canonical_ops),
        failed_ops=tuple(failed_ops),
    )


def _compile_facade_from_inputs(
    inputs: _ReplayCompileInputs,
    *,
    compile_mode: Literal["strict", "quirks"] = "strict",
    strict_profile: StrictProfile | None = None,
) -> Any:
    return compile_fi_facade_from_replay(
        parent_id=inputs.parent_id,
        replay_result=inputs.replay_result,
        replay_mode="legal_pit",
        compile_mode=compile_mode,
        strict_profile=strict_profile,
        compiled_ops=list(inputs.compiled_ops),
        replay_meta=dict(inputs.replay_meta),
        canonical_ops=list(inputs.canonical_ops),
        failed_ops=list(inputs.failed_ops),
    )


@pytest.fixture(scope="module")
def replay_1987_990_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("1987/990", mode="official_consolidation", quiet=True))


@pytest.fixture(scope="module")
def replay_and_facade_2009_953_legal_pit_quirks() -> tuple[ReplayResult, Any]:
    return _compile_facade_with_replay("2009/953", replay_mode="legal_pit", compile_mode="quirks")


@pytest.fixture(scope="module")
def facade_2009_953_legal_pit_quirks(
    replay_and_facade_2009_953_legal_pit_quirks: tuple[ReplayResult, Any],
) -> Any:
    return replay_and_facade_2009_953_legal_pit_quirks[1]


def test_strict_fail_reasons_detect_known_recovery_paths() -> None:
    profile = default_finland_strict_profile()
    recovered = [
        LegalOperation(
            op_id="uncovered_replace_14",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "14"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="14"),
            source=OperationSource(statute_id="2001/1529", effective="2001-01-01"),
        )
    ]
    failures = [
        CompileFailure(
            source_statute="2001/1529",
            description="REPLACE 14 § 4 mom",
            reason="mom_oor(gap=2)",
            target_unit_kind="section",
            target_section="14",
        )
    ]
    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(
                extraction_tags=("extraction_fallback_heuristic",),
                scope_tags=("chapter_scope_from_preamble",),
            ),
        }],
        canonical_ops=recovered,
        failures=failures,
        findings=[
            _runtime_violation(
                "APPLY.UNCOVERED_BODY_RECOVERY",
                stage="apply",
                message="Uncovered-body insertion supplement was used.",
                source_statute="2001/1529",
            ),
            _runtime_violation(
                "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
                stage="apply",
                message="Fallback whole-section replacement was used.",
                source_statute="2001/1529",
            ),
        ],
    )

    assert "APPLY.FAILED_OPERATION" in reasons
    assert "APPLY.UNCOVERED_BODY_RECOVERY" in reasons
    assert "APPLY.FALLBACK_WHOLE_SECTION_REPLACE" in reasons
    assert "PARSE.EXTRACTION_FALLBACK" in reasons
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in reasons


def test_failed_op_to_compile_failure_preserves_reason_code() -> None:
    failure = _failed_op_to_compile_failure(
        FailedOp.from_scope(
            amendment_id="2020/1",
            description="REPLACE 5 §",
            reason="_drop_suspicious_partial_whole_section_replaces: suspicious partial whole-section fallback replace",
            reason_code="PARTIAL_WHOLE_SECTION_REPLACE_REJECTED",
            target_section="5",
            target_unit_kind="section",
        )
    )

    assert failure.reason_code == "PARTIAL_WHOLE_SECTION_REPLACE_REJECTED"
    assert failure.as_detail()["reason_code"] == "PARTIAL_WHOLE_SECTION_REPLACE_REJECTED"


def test_strict_fail_reasons_accept_typed_target_guessing_provenance_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"provenance": serialized_provenance_from_bags(target_guessing_tags=("normalize_item_like_target",))}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.TARGET_GUESSING" in reasons


def test_strict_fail_reasons_detect_shadowed_insert_supplement_tag() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"provenance": serialized_provenance_from_bags(extraction_tags=("fallback_insert_supplement_shadowed",))}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.EXTRACTION_FALLBACK" in reasons


def test_strict_fail_reasons_detect_shadowed_replace_supplement_tag() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"provenance": serialized_provenance_from_bags(extraction_tags=("fallback_replace_supplement_shadowed",))}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.EXTRACTION_FALLBACK" in reasons


def test_strict_fail_reasons_accept_chapter_scope_stripping_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_unique_section",)),
            "target_unit_kind": "section",
            "target_section": "14",
        }],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" in reasons


def test_strict_fail_reasons_accept_subsection_insert_scope_stripping_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_subsection_insert",)),
            "target_unit_kind": "section",
            "target_section": "14",
        }],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" in reasons


def test_strict_fail_reasons_accept_section_facet_insert_scope_stripping_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_section_facet_insert",)),
            "target_unit_kind": "section",
            "target_section": "14",
        }],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" in reasons


def test_strict_fail_reasons_accept_duplicate_label_scope_stripping_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_duplicate_label_outside_stated_chapter",)),
            "target_unit_kind": "section",
            "target_section": "14",
        }],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" in reasons


def test_strict_fail_reasons_accept_explicit_chunk_scope_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_explicit_chunk",)),
            "target_unit_kind": "section",
            "target_section": "14",
            "target_chapter": "5",
        }],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED" in reasons


def test_compile_fi_extracts_explicit_scope_rewrite_projection_from_compiled_ops(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "2004/1313",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_unique_section",)),
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    rewrite = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED")
    assert cast(dict[str, Any], rewrite["detail"])["tag"] == "chapter_scope_stripped_unique_section"
    assert cast(dict[str, Any], rewrite["detail"])["scope_confidence"] == "rewritten"
    assert cast(dict[str, Any], rewrite["detail"])["scope_source"] == "explicit_scope_rewrite"
    assert cast(dict[str, Any], rewrite["detail"])["target_unit_kind"] == "section"
    assert cast(dict[str, Any], rewrite["detail"])["target_norm"] == "14"
    assert cast(dict[str, Any], rewrite["detail"])["target_chapter"] == "5"


def test_compile_fi_extracts_explicit_chunk_scope_projection_from_compiled_ops(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "2004/1313",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_explicit_chunk",)),
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    explicit_chunk = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED")
    assert cast(dict[str, Any], explicit_chunk["detail"])["tag"] == "chapter_scope_from_explicit_chunk"
    assert cast(dict[str, Any], explicit_chunk["detail"])["scope_confidence"] == "explicit"
    assert cast(dict[str, Any], explicit_chunk["detail"])["scope_source"] == "explicit_chunk"
    assert cast(dict[str, Any], explicit_chunk["detail"])["scope_transport_mode"] == "legacy_scope_tag_fallback"
    assert cast(dict[str, Any], explicit_chunk["detail"])["target_unit_kind"] == "section"
    assert cast(dict[str, Any], explicit_chunk["detail"])["target_norm"] == "14"
    assert cast(dict[str, Any], explicit_chunk["detail"])["target_chapter"] == "5"


def test_compile_fi_prefers_replay_scope_finding_over_compiled_op_scope_transport(
    monkeypatch,
) -> None:
    replay_finding = Finding(
        kind="LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
        role="obligation",
        stage="frontend_scope",
        source_statute="2004/1313",
        blocking=True,
        detail={
            "tag": "chapter_scope_stripped_unique_section",
            "scope_source": "explicit_scope_rewrite",
            "scope_confidence": "rewritten",
            "target_unit_kind": "section",
            "target_norm": "14",
            "target_chapter": "5",
        },
    )

    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "2004/1313",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_unique_section",)),
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub(findings=(replay_finding,))

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    rewrite_rows = [a for a in _projection_rows(facade) if a["kind"] == "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED"]
    assert len(rewrite_rows) == 1
    assert cast(dict[str, Any], rewrite_rows[0]["detail"])["target_norm"] == "14"
    assert cast(dict[str, Any], rewrite_rows[0]["detail"])["target_chapter"] == "5"


def test_compile_fi_facade_uses_publication_metadata_fallback_for_2025_78() -> None:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "error",
                message=r"compile_timelines: skipping op from 2025/78 .*",
                category=UserWarning,
            )
            facade = compile_fi_facade("2015/1480", replay_mode="official_consolidation")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    assert facade.bundle.target_statute == "2015/1480"


def test_replay_xml_preserves_letter_suffix_item_spacing_for_2014_346() -> None:
    try:
        replay = pinned_replay("2014/346", mode="official_consolidation", quiet=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")
    section = extract_ir_sections(replay.materialized_state.ir)["section:1"]

    num_text = None
    for child in section.children:
        if child.kind != IRNodeKind.SUBSECTION:
            continue
        for paragraph in child.children:
            if paragraph.kind != IRNodeKind.PARAGRAPH or paragraph.label != "3a":
                continue
            num_text = next(
                (grandchild.text for grandchild in paragraph.children if grandchild.kind == IRNodeKind.NUM),
                None,
            )
            break
        if num_text is not None:
            break

    assert num_text == "3 a)"


def test_replay_xml_absorbs_2008_342_section_21_tail_subsection_with_authority(
    replay_1987_990_finlex_oracle: ReplayResult,
) -> None:
    """2008/342 encodes the list tail as a sibling subsection; apply recovery owns it."""
    section = extract_ir_sections(replay_1987_990_finlex_oracle.materialized_state.ir)["chapter:5/section:21"]

    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections[:4]] == ["1", "2", "3", "4"]

    first_subsection = subsections[0]
    seventh_para = next(
        child for child in first_subsection.children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
    )
    subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
    assert subparagraphs == []
    first_wrapup_text = " ".join(
        (child.text or "").strip()
        for child in seventh_para.children
        if child.kind == IRNodeKind.WRAP_UP
    )
    assert "ydinenergian käyttö muutoinkin täyttää" in first_wrapup_text

    second_subsection_text = " ".join(
        (child.text or "").strip()
        for child in subsections[1].children
        if child.kind in {IRNodeKind.CONTENT, IRNodeKind.INTRO}
    )
    assert "Edellä 1 momentissa tarkoitettuun ydinenergian käyttöön" in second_subsection_text


def test_replay_xml_keeps_1967_550_section_2_sparse_insert_on_fifth_moment() -> None:
    replay = pinned_replay("1967/550", mode="official_consolidation", quiet=True, build_full_products=False)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:1/section:2"]

    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4", "5", "6"]

    fifth_text = irnode_to_text(subsections[4])
    sixth_text = irnode_to_text(subsections[5])

    assert "ei myöskään estä" in fifth_text
    assert "kuuden kuukauden kuluessa" not in fifth_text
    assert "kuuden kuukauden kuluessa" in sixth_text


def test_replay_xml_1984_603_applies_2007_473_whole_section_replace_before_2015_update() -> None:
    replay = pinned_replay("1984/603", mode="official_consolidation", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:5/section:16"]

    text = irnode_to_text(section)
    assert "Tilintarkastaja voi erota toimestaan" not in text
    assert "Tilintarkastajan voi erottaa toimestaan" not in text
    assert "Jos tilintarkastajan toimi tulee kesken toimikautta avoimeksi" in text


@pytest.mark.slow
def test_replay_xml_places_2019_371_section_159_in_final_container_frame() -> None:
    """2019/371 preserves §159 text under the final replay and materialized frame."""
    replay = pinned_replay(
        "2017/320",
        mode="official_consolidation",
        quiet=True,
        strict_johto_temporal=False,
    )
    for ir in (replay.replay_fold_state.ir, replay.materialized_state.ir):
        sections = extract_ir_sections(ir)
        section_159_keys = [key for key in sections if key.endswith("/section:159") or key == "section:159"]
        assert section_159_keys == ["part:4/chapter:18/section:159"]
        text = irnode_to_text(sections["part:4/chapter:18/section:159"])

        assert text.startswith("159 §")
        assert "avoimia rajapintoja teknisesti yhdistävien palveluntarjoajien" in text
        assert "matkustusoikeuden todentamiseen liittyvien taustajärjestelmien" in text

    materialized_text = irnode_to_text(
        extract_ir_sections(replay.materialized_state.ir)["part:4/chapter:18/section:159"]
    )
    assert "liityntäpysäköintiä tarjoavan" not in materialized_text

    rows = [
        row
        for row in replay.source_pathology_rows()
        if row.get("code") == "RECODIFICATION_SOURCE_CHAIN_GAP"
        and row.get("source_statute") == "2019/371"
    ]
    assert rows


@pytest.mark.slow
def test_2019_371_compile_records_omission_only_recodification_pathology_for_sections_209_210() -> None:
    """Renumber-only omission shells are manual-frontier source limits, not apply payloads."""
    replay = pinned_replay(
        "2017/320",
        mode="legal_pit",
        quiet=True,
        stop_before="2020/1256",
    )
    omission_shell_labels = {
        str(row.get("target_label") or "")
        for row in replay.source_pathology_rows()
        if row.get("code") == "RECODIFICATION_OMISSION_ONLY_SECTION_SHELL"
        and row.get("source_statute") == "2019/371"
    }
    assert any(" 209 §" in label for label in omission_shell_labels)
    assert any(" 210 §" in label for label in omission_shell_labels)


@pytest.mark.slow
def test_2020_1256_compile_keeps_vi_part_scope_for_chapter_26_28_renumbers() -> None:
    from lawvm.tools.inspect_amendment import _working_johtolause

    statute_id = "2017/320"
    source_id = "2020/1256"
    corpus = get_corpus_store()
    xml_bytes = corpus.read_source(source_id)
    assert xml_bytes is not None

    before_master = pinned_replay(
        statute_id,
        mode="legal_pit",
        stop_before=source_id,
        quiet=True,
        build_full_products=False,
    )
    _muutos_tree, johto, used_preamble_body_fallback, should_apply, _route_reason = _working_johtolause(
        statute_id,
        before_master.title,
        source_id,
        xml_bytes,
        "",
    )
    assert should_apply is True

    phase = normalize_and_compile_ops(
        johto,
        etree.fromstring(xml_bytes),
        before_master.replay_fold_state,
        source_id,
        source_title="",
        used_preamble_body_fallback=used_preamble_body_fallback,
        parent_id=statute_id,
        strict_profile=None,
    )

    by_dest = {
        str(op.lo.destination.path[-1][1]): op
        for op in phase.output
        if op.op_type == "RENUMBER"
        and op.target_cols.target_unit_kind == "chapter"
        and getattr(op, "lo", None) is not None
        and op.lo.destination is not None
        and str(op.lo.destination.path[-1][1]) in {"26", "27", "28"}
    }

    assert by_dest["26"].target_cols.target_part == "VI"
    assert by_dest["27"].target_cols.target_part == "VI"
    assert by_dest["28"].target_cols.target_part == "VI"


def test_strict_fail_reasons_materializes_iterables_once() -> None:
    profile = replace(
        default_finland_strict_profile(),
        allows_word_substitution=False,
        requires_explicit_effective_date=True,
    )
    op = LegalOperation(
        op_id="op0",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="2020/1", effective=""),
    )

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=({"provenance": serialized_provenance_from_bags(target_guessing_tags=("normalize_item_like_target",))} for _ in range(1)),
        canonical_ops=(candidate for candidate in [op]),
        failures=(
            CompileFailure(
                source_statute="2020/1",
                description="oops",
                reason="x",
                target_unit_kind="section",
                target_section="2",
            )
            for _ in range(1)
        ),
        findings=[
            _runtime_violation(
                "TIME.MISSING_EFFECTIVE_DATE",
                stage="timeline",
                message="explicit temporal finding",
            )
        ],
    )

    assert "APPLY.FAILED_OPERATION" in reasons
    assert "TIME.MISSING_EFFECTIVE_DATE" in reasons
    assert "PARSE.TARGET_GUESSING" in reasons


def test_strict_fail_reasons_do_not_infer_missing_effective_date_from_canonical_ops() -> None:
    profile = replace(
        default_finland_strict_profile(),
        requires_explicit_effective_date=True,
    )
    op = LegalOperation(
        op_id="op0",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="2020/1", effective=""),
    )

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=(),
        canonical_ops=(op,),
        failures=(),
        findings=[],
    )

    assert "TIME.MISSING_EFFECTIVE_DATE" not in reasons


def test_strict_fail_reasons_accept_legacy_dispatch_fallback_finding() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                detail={"message": "Apply fell back to legacy field-based dispatch."},
                blocking=True,
            )
        ],
    )

    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in reasons


def test_strict_fail_reasons_accept_semantic_collapse_move_renumber_finding() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                role="observation",
                stage="frontend_extraction",
                source_statute="2020/1",
                detail={
                    "message": "Frontend elaboration recorded observation: PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                    "target_unit_kind": "section",
                    "target_norm": "33",
                    "target_chapter": "5",
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
                blocking=False,
            )
        ],
    )

    assert "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_legacy_dispatch_fallback() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                detail={"message": "Apply fell back to legacy field-based dispatch."},
                blocking=True,
            )
        ],
    )

    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_semantic_collapse_move_renumber() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                role="observation",
                stage="elaboration",
                detail={
                    "message": "Frontend elaboration recorded observation: PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                    "target_unit_kind": "section",
                    "target_norm": "33",
                    "target_chapter": "5",
                    "collapse_kind": "destinationless_move_relabel",
                },
                source_statute="2020/1",
                blocking=False,
            )
        ],
    )

    assert "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" in reasons


def test_compile_fi_facade_returns_path_aware_dossier(facade_2009_953_legal_pit_quirks: Any) -> None:
    assert facade_2009_953_legal_pit_quirks.bundle.target_statute == "2009/953"
    assert facade_2009_953_legal_pit_quirks.replay_mode == "legal_pit"
    assert facade_2009_953_legal_pit_quirks.strict_profile_name == "finland_ingestion_v1"
    assert isinstance(facade_2009_953_legal_pit_quirks.bundle.structural_ops, tuple)
    assert isinstance(_projection_rows(facade_2009_953_legal_pit_quirks), tuple)
    assert isinstance(_source_pathology_rows(facade_2009_953_legal_pit_quirks), tuple)
    assert isinstance(tuple(facade_2009_953_legal_pit_quirks.to_wire_artifact().processing_status.blockers or ()), tuple)


def test_compile_fi_facade_strict_mode_passes_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    compile_fi_facade("2009/953", replay_mode="legal_pit", compile_mode="strict")

    assert captured.get("strict_johto_temporal") is True


def test_compile_fi_facade_default_mode_is_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    compile_fi_facade("2009/953", replay_mode="legal_pit")

    assert captured.get("strict_johto_temporal") is True


def test_compile_fi_facade_quirks_mode_does_not_enable_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    compile_fi_facade("2009/953", replay_mode="legal_pit", compile_mode="quirks")

    assert captured.get("strict_johto_temporal") is False


def test_finland_compile_surface_does_not_export_compat_adjudication_ingress() -> None:
    compile_module = importlib.import_module("lawvm.finland.compile")

    assert not hasattr(compile_module, "findings_from_compat_adjudications")


def test_compile_fi_facade_from_replay_matches_compile_contract(monkeypatch) -> None:
    def fake_compile_artifacts_from_replay(*args, **kwargs):
        assert list(kwargs.get("extra_findings") or []) == []
        assert "extra_adjudications" not in kwargs
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=[],
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    facade = compile_fi_facade_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={},
        canonical_ops=[],
        failed_ops=[],
    )

    assert facade.bundle.target_statute == "2009/953"
    assert facade.replay_mode == "legal_pit"
    assert not hasattr(facade, "source_completeness_flags")
    assert not hasattr(facade, "strict_fail_reasons")
    assert not hasattr(facade, "source_completeness")


def test_compile_fi_facade_rejects_conflicting_duplicate_effect_ids(monkeypatch) -> None:
    def fake_compile_artifacts_from_replay(*_args, **_kwargs):
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=[],
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    canonical_op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "4 a"),)),
        source=OperationSource(statute_id="2020/1"),
    )
    conflicting_product_effect = EffectRef(
        effect_id="fi-effect:2020/1:op-1",
        source_instrument=SourceInstrumentRef(instrument_id="2020/1"),
        target_statute="not-the-parent",
        target_address=LegalAddress(path=(("section", "4 a"),)),
    )

    with pytest.raises(ValueError, match="conflicting duplicate effect_id"):
        compile_fi_facade_from_replay(
            parent_id="2009/953",
            replay_result=_replay_result_stub(source_effects=(conflicting_product_effect,)),
            replay_mode="legal_pit",
            compiled_ops=[],
            replay_meta={},
            canonical_ops=[canonical_op],
            failed_ops=[],
        )


def test_compile_fi_facade_rejects_conflicting_duplicate_lifecycle_events(monkeypatch) -> None:
    def fake_compile_artifacts_from_replay(*_args, **_kwargs):
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=[],
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    temporal = TemporalEvent(
        event_id="event:expiry",
        kind="expire",
        scope=TemporalScope(
            target_statute="2009/953",
            exact_addresses=(LegalAddress(path=(("section", "4 a"),)),),
        ),
        expires="2025-01-01",
        source=OperationSource(statute_id="2020/1", expires="2025-01-01"),
        group_id="g:2020/1:expiry",
    )
    source_effects, _relations, derived_lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2009/953",
        canonical_ops=(),
        temporal_events=(temporal,),
    )
    derived_lifecycle = derived_lifecycle_events[0]
    conflicting_lifecycle = EffectLifecycleEvent(
        lifecycle_event_id=derived_lifecycle.lifecycle_event_id,
        kind=derived_lifecycle.kind,
        source_provision=derived_lifecycle.source_provision,
        effect=derived_lifecycle.effect,
        expires=derived_lifecycle.expires,
        temporal_event=temporal,
        executable=True,
        detail={"projection": "product_conflict"},
    )

    with pytest.raises(ValueError, match="conflicting duplicate lifecycle_event_id"):
        compile_fi_facade_from_replay(
            parent_id="2009/953",
            replay_result=_replay_result_stub(
                temporal_events=(temporal,),
                source_effects=source_effects,
                effect_lifecycle_events=(conflicting_lifecycle,),
            ),
            replay_mode="legal_pit",
            compiled_ops=[],
            replay_meta={},
            canonical_ops=[],
            failed_ops=[],
        )


def test_compile_fi_facade_uses_resolved_temporal_lifecycle_over_stale_product_projection(
    monkeypatch,
) -> None:
    def fake_compile_artifacts_from_replay(*_args, **_kwargs):
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=[],
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    address = LegalAddress(path=(("chapter", "12"), ("section", "127a")))
    op_effects, _relations, _events = build_finland_effect_lifecycle(
        target_statute="2003/393",
        canonical_ops=(
            LegalOperation(
                op_id="op_0",
                sequence=1,
                action=StructuralAction.INSERT,
                target=address,
                source=OperationSource(
                    statute_id="2008/119",
                    title="temporary act",
                    effective="2008-03-01",
                    expires="2010-07-01",
                ),
                group_id="finland-johto:2008/119",
            ),
        ),
        temporal_events=(),
    )
    stale_temporal = TemporalEvent(
        event_id="fi-temporary:2008/119:op_0:expire",
        kind="expire",
        scope=TemporalScope(target_statute="2003/393", exact_addresses=(address,)),
        expires="2010-07-01",
        source=OperationSource(statute_id="2008/119", expires="2010-07-01"),
        group_id="finland-johto:2008/119",
    )
    resolved_temporal = replace(
        stale_temporal,
        expires="2012-07-01",
        source=OperationSource(statute_id="2008/119", expires="2012-07-01"),
    )
    _source_effects, _relations, stale_lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2003/393",
        canonical_ops=(),
        temporal_events=(stale_temporal,),
        known_source_effects=op_effects,
    )

    facade = compile_fi_facade_from_replay(
        parent_id="2003/393",
        replay_result=_replay_result_stub(
            temporal_events=(resolved_temporal,),
            source_effects=op_effects,
            effect_lifecycle_events=stale_lifecycle_events,
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={},
        canonical_ops=[],
        failed_ops=[],
    )

    event = facade.bundle.effect_lifecycle_events[0]
    assert event.lifecycle_event_id == "fi-effect-lifecycle:fi-temporary:2008/119:op_0:expire"
    assert event.expires == "2012-07-01"
    assert event.temporal_event is resolved_temporal


def test_compile_fi_facade_reuses_product_source_effects_for_temporal_lifecycle(
    monkeypatch,
) -> None:
    def fake_compile_artifacts_from_replay(*_args, **_kwargs):
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=[],
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    address = LegalAddress(path=(("section", "4a"),))
    op_effects, _relations, _events = build_finland_effect_lifecycle(
        target_statute="1995/903",
        canonical_ops=(
            LegalOperation(
                op_id="op_0",
                sequence=1,
                action=StructuralAction.INSERT,
                target=address,
                source=OperationSource(statute_id="2021/538", effective="2021-07-01"),
                group_id="2021/538",
            ),
        ),
        temporal_events=(),
    )
    temporal = TemporalEvent(
        event_id="fi-temporary:2021/538:op_0:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="1995/903",
            exact_addresses=(address,),
        ),
        expires="2025-01-01",
        source=OperationSource(statute_id="2021/538", expires="2025-01-01"),
        group_id="2021/538",
    )
    _source_effects, _relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1995/903",
        canonical_ops=(),
        temporal_events=(temporal,),
        known_source_effects=op_effects,
    )

    facade = compile_fi_facade_from_replay(
        parent_id="1995/903",
        replay_result=_replay_result_stub(
            temporal_events=(temporal,),
            source_effects=op_effects,
            effect_lifecycle_events=lifecycle_events,
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={},
        canonical_ops=[],
        failed_ops=[],
    )

    assert facade.bundle.effect_lifecycle_events == lifecycle_events
    assert facade.bundle.effect_lifecycle_events[0].effect == op_effects[0]


def test_compile_fi_facade_projects_rows_from_stored_findings_only() -> None:
    facade = compile_fi_facade_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(
            findings=(
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "reason_tag": "missing_canonical_intent",
                    },
                    source_statute="1993/805",
                    blocking=False,
                ),
            ),
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={"lineage": []},
        canonical_ops=[],
        failed_ops=[],
    )

    assert len(facade.finding_ledger) == 1
    assert facade.finding_ledger[0].kind == "APPLY.LEGACY_DISPATCH_FALLBACK"
    assert not hasattr(facade, "adjudications")
    assert not hasattr(facade, "source_completeness_flags")
    assert _projection_rows(facade)[0]["kind"] == "APPLY.LEGACY_DISPATCH_FALLBACK"
    assert [row["kind"] for row in _projection_rows(facade)] == ["APPLY.LEGACY_DISPATCH_FALLBACK"]


def test_compile_artifacts_from_replay_does_not_infer_recovery_findings_from_op_ids() -> None:
    recovered = [
        LegalOperation(
            op_id="uncovered_replace_14",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "14"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="14"),
            source=OperationSource(statute_id="2001/1529", effective="2001-01-01"),
        )
    ]

    artifacts = _compile_artifacts_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(findings=()),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={"lineage": []},
        canonical_ops=recovered,
        failed_ops=[],
    )

    assert [finding.kind for finding in artifacts.findings] == []
    assert artifacts.verdict is not None
    assert artifacts.verdict.barrier_codes == ()


def test_compile_artifacts_from_replay_surfaces_governed_source_corrected_by_patch() -> None:
    artifacts = _compile_artifacts_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(findings=()),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={"lineage": []},
        canonical_ops=[
            LegalOperation(
                op_id="replace_1",
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "1"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="1"),
                source=OperationSource(
                    statute_id="2001/1529",
                    effective="2001-01-01",
                    corrected_by="2024/999",
                ),
            )
        ],
        failed_ops=[],
    )

    assert "APPLY.SOURCE_CORRECTED_BY_PATCH" in {finding.kind for finding in artifacts.findings}
    assert artifacts.verdict is not None
    assert "APPLY.SOURCE_CORRECTED_BY_PATCH" not in artifacts.verdict.barrier_codes


def test_compile_artifacts_from_replay_prefers_typed_source_adjudication_over_replay_meta() -> None:
    typed_source_adjudication = SourceAdjudication(
        statute_id="2009/953",
        replay_mode="legal_pit",
        cutoff_date="2025-01-01",
        oracle_version_amendment_id="typed-mid",
        oracle_suspect="typed-suspect",
        html_noncommensurable_reason="typed-html-reason",
        lineage=({"included": True, "effective_date": "2025-01-01"},),
    )

    artifacts = _compile_artifacts_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(
            findings=(),
            source_adjudication=typed_source_adjudication,
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={
            "cutoff_date": "1999-01-01",
            "oracle_version_amendment_id": "raw-mid",
            "oracle_suspect": "raw-suspect",
            "html_noncommensurable_reason": "raw-html-reason",
            "lineage": [{"included": False, "effective_date": "1999-01-01"}],
        },
        canonical_ops=[],
        failed_ops=[],
    )

    assert artifacts.source_adjudication is not None
    assert artifacts.source_adjudication.cutoff_date == "2025-01-01"
    assert artifacts.source_adjudication.oracle_version_amendment_id == "typed-mid"
    assert artifacts.source_adjudication.oracle_suspect == "typed-suspect"
    assert artifacts.source_adjudication.html_noncommensurable_reason == "typed-html-reason"
    assert list(artifacts.source_adjudication.lineage) == [{"included": True, "effective_date": "2025-01-01"}]
    source_incomplete = [finding for finding in artifacts.findings if finding.kind == "APPLY.SOURCE_INCOMPLETE"]
    assert len(source_incomplete) == 1
    assert source_incomplete[0].detail["oracle_suspect"] == "typed-suspect"


def test_compile_artifacts_from_replay_hydrates_source_adjudication_from_replay_meta() -> None:
    artifacts = _compile_artifacts_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(
            findings=(),
            source_adjudication=None,
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={
            "cutoff_date": "2025-01-01",
            "oracle_version_amendment_id": "raw-mid",
            "oracle_suspect": "raw-suspect",
            "html_noncommensurable_reason": "raw-html-reason",
            "lineage": [{"included": True, "effective_date": "2025-01-01"}],
        },
        canonical_ops=[],
        failed_ops=[],
    )

    assert artifacts.source_adjudication is not None
    assert artifacts.source_adjudication.cutoff_date == "2025-01-01"
    assert artifacts.source_adjudication.oracle_version_amendment_id == "raw-mid"
    assert artifacts.source_adjudication.oracle_suspect == "raw-suspect"
    assert artifacts.source_adjudication.html_noncommensurable_reason == "raw-html-reason"
    assert list(artifacts.source_adjudication.lineage) == [{"included": True, "effective_date": "2025-01-01"}]
    source_incomplete = [finding for finding in artifacts.findings if finding.kind == "APPLY.SOURCE_INCOMPLETE"]
    assert len(source_incomplete) == 1
    assert source_incomplete[0].detail["oracle_suspect"] == "raw-suspect"


def test_compile_artifacts_from_replay_treats_governed_invariant_violation_as_internal_failure() -> None:
    artifacts = _compile_artifacts_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(
            findings=(
                _runtime_violation(
                    "APPLY.TREE_INVARIANT_VIOLATION",
                    stage="apply",
                    message="boom",
                    source_statute="2024/1",
                ),
            ),
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={"lineage": []},
        canonical_ops=[],
        failed_ops=[],
    )

    assert artifacts.verdict is not None
    assert artifacts.verdict.verdict_status == "internal_failure"


def test_compile_fi_facade_from_replay_projects_rows_from_findings(monkeypatch) -> None:
    def fake_compile_artifacts_from_replay(*args, **kwargs):
        return SimpleNamespace(
            compiled_ops=[],
            canonical_ops=[],
            compile_failures=[],
            findings=(
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "reason_tag": "missing_canonical_intent",
                    },
                    source_statute="1993/805",
                    blocking=False,
                ),
            ),
            strict_fail_reasons=[],
            source_adjudication=None,
            replay_meta={},
            verdict=compute_verdict_from_registry(default_finland_strict_profile(), [], has_internal_failure=False),
        )

    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )
    facade = compile_fi_facade_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={},
        canonical_ops=[],
        failed_ops=[],
    )

    rows = _projection_rows(facade)
    assert len(rows) == 1
    assert rows[0]["kind"] == "APPLY.LEGACY_DISPATCH_FALLBACK"
    assert rows[0]["source"] == "1993/805"
    assert rows[0]["detail"] == {
        "message": "Apply fell back to legacy field-based dispatch.",
        "reason_tag": "missing_canonical_intent",
    }


def test_compile_fi_facade_from_replay_prefers_replay_result_findings() -> None:
    facade = compile_fi_facade_from_replay(
        parent_id="2009/953",
        replay_result=_replay_result_stub(
            findings=(
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "reason_tag": "missing_canonical_intent",
                    },
                    source_statute="1993/805",
                    blocking=False,
                ),
            ),
        ),
        replay_mode="legal_pit",
        compiled_ops=[],
        replay_meta={"lineage": []},
        canonical_ops=[],
        failed_ops=[],
    )

    assert [row["kind"] for row in _projection_rows(facade)] == ["APPLY.LEGACY_DISPATCH_FALLBACK"]


def test_compile_fi_facade_returns_native_dossier(facade_2009_953_legal_pit_quirks: Any) -> None:
    assert facade_2009_953_legal_pit_quirks.bundle.target_statute == "2009/953"
    assert facade_2009_953_legal_pit_quirks.replay_mode == "legal_pit"
    assert facade_2009_953_legal_pit_quirks.strict_profile_name == "finland_ingestion_v1"
    assert isinstance(facade_2009_953_legal_pit_quirks.bundle.structural_ops, tuple)
    assert isinstance(_projection_rows(facade_2009_953_legal_pit_quirks), tuple)
    assert isinstance(tuple(facade_2009_953_legal_pit_quirks.to_wire_artifact().processing_status.blockers or ()), tuple)


def test_compile_fi_facade_returns_native_finland_facade(monkeypatch) -> None:
    source_adjudication = SimpleNamespace(
        statute_id="2009/953",
        replay_mode="legal_pit",
        cutoff_date="",
        oracle_version_amendment_id="",
        oracle_suspect="",
        html_noncommensurable_reason="",
        lineage=(),
    )

    master = _replay_result_stub(
        temporal_events=(
            TemporalEvent(
                event_id="test:1",
                group_id="test",
                kind="commence",
                scope=TemporalScope(target_statute="2009/953"),
                effective="2020-01-01",
                source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
            ),
        ),
    )

    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "2009/953"
        assert mode == "legal_pit"
        assert strict_profile is None
        if lo_ops_out is not None:
            lo_ops_out.append(
                LegalOperation(
                    op_id="op-1",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                    payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            )
        return master

    def fake_compile_artifacts_from_replay(*args, **kwargs):
        return SimpleNamespace(
            findings=(
                Finding(
                    kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    role="obligation",
                    stage="lower",
                    detail={
                        "message": "Compilation required context-dependent anchor resolution.",
                        "tag": "chapter_scope_from_preamble",
                    },
                    source_statute="2020/1",
                    blocking=True,
                ),
            ),
            verdict=None,
            source_adjudication=source_adjudication,
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )

    facade = compile_fi_facade("2009/953", replay_mode="legal_pit")

    assert facade.replay_mode == "legal_pit"
    assert facade.strict_profile_name == "finland_ingestion_v1"
    assert len(facade.bundle.structural_ops) == 1
    assert len(facade.bundle.temporal_events) == 1
    assert facade.bundle.temporal_events[0].source is not None
    assert facade.bundle.temporal_events[0].source.effective == "2020-01-01"
    obligation_findings = tuple(f for f in facade.finding_ledger if f.role == "obligation")
    assert obligation_findings[0].kind == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"


def test_compile_fi_facade_routes_warn_projection_rows_to_observations(monkeypatch) -> None:
    source_adjudication = SimpleNamespace(
        statute_id="2009/205",
        replay_mode="official_consolidation",
        cutoff_date="",
        oracle_version_amendment_id="",
        oracle_suspect="",
        html_noncommensurable_reason="",
        lineage=(),
    )

    master = _replay_result_stub()

    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "official_consolidation",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "2009/205"
        return master

    def fake_compile_artifacts_from_replay(*args, **kwargs):
        return SimpleNamespace(
            findings=(
                Finding(
                    kind="text_duplication_warning",
                    role="observation",
                    stage="replay_fold",
                    detail={
                        "message": "Replay output contains a suspicious duplicated text tract.",
                        "kind": "duplicate_suffix_text",
                    },
                    source_statute="2009/205",
                    blocking=False,
                ),
            ),
            verdict=None,
            source_adjudication=source_adjudication,
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )

    facade = compile_fi_facade("2009/205", replay_mode="official_consolidation")

    assert [finding.kind for finding in facade.finding_ledger if finding.role == "observation"] == [
        "text_duplication_warning"
    ]
    assert [finding for finding in facade.finding_ledger if finding.role == "obligation"] == []


def test_compile_fi_facade_keeps_temporal_bundle_empty_when_replay_events_absent(
    monkeypatch,
) -> None:
    source_adjudication = SimpleNamespace(
        statute_id="2009/953",
        replay_mode="legal_pit",
        cutoff_date="",
        oracle_version_amendment_id="",
        oracle_suspect="",
        html_noncommensurable_reason="",
        lineage=(),
    )

    master = _replay_result_stub()

    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "2009/953"
        assert mode == "legal_pit"
        if lo_ops_out is not None:
            lo_ops_out.append(
                LegalOperation(
                    op_id="op-1",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                    payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            )
        return master

    def fake_compile_artifacts_from_replay(*args, **kwargs):
        return SimpleNamespace(
            findings=(),
            verdict=None,
            source_adjudication=source_adjudication,
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )

    facade = compile_fi_facade("2009/953", replay_mode="legal_pit")

    assert facade.bundle.temporal_events == ()


def test_compile_fi_surfaces_known_recovery_paths_and_source_flags(
    replay_compile_inputs_2002_1090_legal_pit: _ReplayCompileInputs,
) -> None:
    facade = _compile_facade_from_inputs(
        replay_compile_inputs_2002_1090_legal_pit,
        compile_mode="quirks",
    )

    # 2002/1090 is a well-exercised compile target. It should always produce
    # source_adjudication and derived oracle comparability regardless of recovery mix.
    # NOTE: oracle_suspect may or may not be set depending on lineage freshness.
    assert facade.bundle.target_statute == "2002/1090"


def test_compile_fi_surfaces_frontend_elaboration_observations_as_projection_rows(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["elaboration_observations"] = [
                {
                    "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "stage": "group_payload_normalization",
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "detail": {"reason": "single_sparse_payload"},
                },
                {
                    "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "stage": "group_payload_normalization",
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "detail": {"reason": "single_sparse_payload"},
                },
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    elaboration_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
    ]
    assert len(elaboration_projection_rows) == 1
    assert elaboration_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], elaboration_projection_rows[0]["detail"])["stage"] == "group_payload_normalization"
    assert cast(dict[str, Any], elaboration_projection_rows[0]["detail"])["target_norm"] == "35"
    assert cast(dict[str, Any], elaboration_projection_rows[0]["detail"])["detail"] == {
        "reason": "single_sparse_payload"
    }

def test_compile_fi_preserves_payload_completeness_witness_detail(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["elaboration_observations"] = [
                {
                    "kind": "ELAB.PAYLOAD_COMPLETENESS",
                    "stage": "group_payload_normalization",
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "payload_completeness_kind": "sparse_certified",
                    "reasons": ["tail_omission_payload"],
                    "tail_policy": "preserve_unstated_tail",
                    "detail": {
                        "payload_completeness_kind": "sparse_certified",
                        "reasons": ["tail_omission_payload"],
                        "tail_policy": "preserve_unstated_tail",
                        "has_omission": True,
                    },
                }
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    payload_witnesses = [
        a for a in _projection_rows(facade) if a["kind"] == "ELAB.PAYLOAD_COMPLETENESS"
    ]
    assert len(payload_witnesses) == 1
    assert payload_witnesses[0]["detail"] == {
        "message": "Payload completeness witness emitted before apply.",
        "source_statute": "1993/805",
        "stage": "group_payload_normalization",
        "target_unit_kind": "section",
        "target_norm": "35",
        "target_chapter": "",
        "payload_completeness_kind": "sparse_certified",
        "reasons": ("tail_omission_payload",),
        "tail_policy": "preserve_unstated_tail",
        "detail": {
            "payload_completeness_kind": "sparse_certified",
            "reasons": ("tail_omission_payload",),
            "tail_policy": "preserve_unstated_tail",
            "has_omission": True,
        },
    }
    assert sum(1 for a in _projection_rows(facade) if a["kind"] == "ELAB.PAYLOAD_COMPLETENESS") == 1


def test_compile_fi_surfaces_source_pathology_with_neutral_target_unit_kind(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["source_pathologies"] = [
                {
                    "code": "test_pathology",
                    "message": "test",
                    "source_statute": "1993/805",
                    "target_unit_kind": "chapter",
                    "target_label": "3",
                }
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    source_pathologies = [a for a in _projection_rows(facade) if a["kind"] == "APPLY.SOURCE_PATHOLOGY_DETECTED"]
    assert len(source_pathologies) == 1
    assert cast(dict[str, Any], source_pathologies[0]["detail"])["target_unit_kind"] == "chapter"
    assert _source_pathology_rows(facade)[0]["target_unit_kind"] == "chapter"


def test_compile_fi_surfaces_targetless_acquisition_source_pathology(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["source_pathologies"] = [
                {
                    "code": "fi_amendment_selection_source_artifact_missing",
                    "message": "source XML bytes missing",
                    "source_statute": "1990/1295",
                    "amendment_id": "1974/974",
                    "target_unit_kind": "",
                }
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    source_pathologies = [a for a in _projection_rows(facade) if a["kind"] == "APPLY.SOURCE_PATHOLOGY_DETECTED"]
    assert len(source_pathologies) == 1
    detail = cast(dict[str, Any], source_pathologies[0]["detail"])
    assert detail["code"] == "fi_amendment_selection_source_artifact_missing"
    assert detail.get("target_unit_kind", "") == ""
    assert _source_pathology_rows(facade)[0]["target_unit_kind"] == ""


@pytest.mark.slow
def test_compile_fi_surfaces_recodification_source_chain_gap_for_2017_320() -> None:
    facade = compile_fi_facade("2017/320", replay_mode="legal_pit")

    rows = [
        row
        for row in _source_pathology_rows(facade)
        if row.get("code") == "RECODIFICATION_SOURCE_CHAIN_GAP"
        and row.get("source_statute") == "2019/371"
    ]

    assert rows
    labels = {cast(str, row["target_label"]) for row in rows}
    assert "2 luku 7 §" in labels
    assert "4 luku 11 §" not in labels
    details = {
        (cast(str, row["target_label"]), cast(dict[str, Any], row["detail"]).get("diagnostic_reason"))
        for row in rows
    }
    assert ("2 luku 7 §", "target_leaf_absent_under_existing_parent") in details


@pytest.mark.slow
def test_compile_fi_2017_320_preserves_sparse_subsection_target_labels() -> None:
    facade = compile_fi_facade("2017/320", replay_mode="legal_pit")

    subsection_ops = [
        op
        for op in facade.bundle.structural_ops
        if op.source is not None
        and op.source.statute_id == "2020/1256"
        and op.target.path[:3] == (("part", "2"), ("chapter", "10"), ("section", "107"))
        and len(op.target.path) == 4
        and op.target.path[-1][0] == "subsection"
    ]
    by_label = {op.target.path[-1][1]: " ".join(irnode_to_text(op.payload).split()) for op in subsection_ops}

    assert "Lisäksi pätevyyskirjan ja lisäpätevyystodistuksen myöntämisen edellytyksenä" in by_label["3"]
    assert "Liikenne- ja viestintävirasto vahvistaa pätevyyskirjan" in by_label["5"]
    assert not any(
        finding.kind == "COVERAGE.PAYLOAD_REALIZATION_GAP"
        and finding.source_statute == "2020/1256"
        and cast(dict[str, Any], finding.detail).get("unit_id") == "op_27"
        for finding in facade.finding_ledger
    )


@pytest.mark.slow
def test_compile_fi_2017_320_keeps_dense_inserted_chapter_members_for_later_targets() -> None:
    replay_before = replay_xml_for_test(
        "2017/320",
        mode="official_consolidation",
        quiet=True,
        stop_before="2018/984",
    )

    def chapter_sections(part_label: str, chapter_label: str) -> list[str]:
        found: list[str] = []

        def walk(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> None:
            next_path = path
            if node.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER, IRNodeKind.SECTION} and node.label:
                next_path = (*path, (node.kind.value, node.label))
            if next_path == (("part", part_label), ("chapter", chapter_label)):
                found.extend(
                    child.label
                    for child in node.children
                    if child.kind is IRNodeKind.SECTION and child.label
                )
                return
            for child in node.children:
                walk(child, next_path)

        walk(replay_before.products.replay_fold_state.ir)
        return found

    assert chapter_sections("2", "7") == [str(n) for n in range(1, 22)]
    assert chapter_sections("2", "10") == [str(n) for n in range(1, 19)]

    failed_ops: list[Any] = []
    replay_xml_for_test(
        "2017/320",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
        failed_ops_out=failed_ops,
    )

    assert not [op for op in failed_ops if getattr(op, "amendment_id", "") == "2018/984"]


def test_compile_fi_surfaces_apply_legacy_dispatch_fallback_as_projection_row(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub(
            findings=(
                Finding(
                    kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                    role="obligation",
                    stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "op_id": "op_1",
                        "helper": "apply_op",
                        "reason_tag": "missing_canonical_intent",
                        "reason_code": "missing_canonical_intent",
                        "used_fallback_tags": [
                            "APPLY.LEGACY_DISPATCH_FALLBACK",
                            "missing_canonical_intent",
                        ],
                        "failure_reason": "ResolvedOp reached apply without CanonicalIntent",
                        "resolved_target_path": [["section", "35"]],
                        "source_statute": "1993/805",
                    },
                    source_statute="1993/805",
                    blocking=True,
                ),
            ),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    fallback_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "APPLY.LEGACY_DISPATCH_FALLBACK"
    ]
    assert len(fallback_projection_rows) == 1
    assert fallback_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], fallback_projection_rows[0]["detail"])["reason_tag"] == "missing_canonical_intent"
    assert cast(dict[str, Any], fallback_projection_rows[0]["detail"])["reason_code"] == "missing_canonical_intent"
    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in tuple(facade.to_wire_artifact().processing_status.blockers or ())


def test_compile_fi_surfaces_legacy_dispatch_reason_code_from_fallback_tags_when_missing(
    monkeypatch,
) -> None:
    def fake_replay_xml(*args, **kwargs):
        replay_meta_out = kwargs["replay_meta_out"]
        replay_meta_out["lineage"] = []
        return _replay_result_stub(
            findings=(
                Finding(
                    kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                    role="obligation",
                    stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "op_id": "op_1",
                        "helper": "apply_op",
                        "reason_tag": "missing_canonical_intent",
                        "reason_code": "missing_canonical_intent",
                        "used_fallback_tags": [
                            "APPLY.LEGACY_DISPATCH_FALLBACK",
                            "missing_canonical_intent",
                        ],
                        "failure_reason": "ResolvedOp reached apply without CanonicalIntent",
                        "resolved_target_path": [["section", "35"]],
                        "source_statute": "1993/805",
                    },
                    source_statute="1993/805",
                    blocking=True,
                ),
            ),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    fallback_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "APPLY.LEGACY_DISPATCH_FALLBACK"
    ]
    assert len(fallback_rows) == 1
    assert cast(dict[str, Any], fallback_rows[0]["detail"])["reason_code"] == "missing_canonical_intent"


def test_compile_fi_facade_carries_legacy_dispatch_fallback_in_finding_ledger(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub(
            findings=(
                Finding(
                    kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                    role="obligation",
                    stage="apply",
                    detail={
                        "message": "Apply fell back to legacy field-based dispatch.",
                        "op_id": "op_1",
                        "helper": "apply_op",
                        "reason_tag": "missing_canonical_intent",
                        "reason_code": "missing_canonical_intent",
                        "used_fallback_tags": [
                            "APPLY.LEGACY_DISPATCH_FALLBACK",
                            "missing_canonical_intent",
                        ],
                        "failure_reason": "ResolvedOp reached apply without CanonicalIntent",
                        "resolved_target_path": [["section", "35"]],
                        "source_statute": "1993/805",
                    },
                    source_statute="1993/805",
                    blocking=True,
                ),
            ),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in {finding.kind for finding in facade.finding_ledger}
    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in tuple(facade.to_wire_artifact().processing_status.blockers or ())


def test_compile_fi_surfaces_relabel_skipped_as_projection_row(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub(
            findings=(
                Finding(
                    kind="APPLY.RELABEL_SKIPPED",
                    role="obligation",
                    stage="apply",
                    detail={
                        "message": "Typed relabel intent was skipped for a governed reason.",
                        "op_id": "op_1",
                        "helper": "_apply_intent_relabel",
                        "reason_tag": "source_section_missing",
                        "reason_code": "source_section_missing",
                        "used_fallback_tags": [
                            "APPLY.RELABEL_SKIPPED",
                            "source_section_missing",
                        ],
                        "failure_reason": "source section 73 not found",
                        "resolved_target_path": [["chapter", "7"], ["section", "73"]],
                        "source_statute": "1993/805",
                    },
                    source_statute="1993/805",
                    blocking=True,
                ),
            ),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    relabel_rows = [a for a in _projection_rows(facade) if a["kind"] == "APPLY.RELABEL_SKIPPED"]
    assert len(relabel_rows) == 1
    assert relabel_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], relabel_rows[0]["detail"])["reason_tag"] == "source_section_missing"
    assert cast(dict[str, Any], relabel_rows[0]["detail"])["reason_code"] == "source_section_missing"
    assert "APPLY.RELABEL_SKIPPED" in tuple(facade.to_wire_artifact().processing_status.blockers or ())


def test_compile_fi_surfaces_registered_provenance_projection_kinds(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "1993/805",
                        "provenance": serialized_provenance_from_bags(target_guessing_tags=("normalize_item_like_target",)),
                    },
                    {
                        "source_statute": "1993/805",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    kinds = {str(a["kind"]) for a in _projection_rows(facade)}
    assert "PARSE.TARGET_GUESSING" in kinds
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in kinds
    assert "target_guessing" not in kinds
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR" not in kinds

    target_guessing = next(a for a in _projection_rows(facade) if a["kind"] == "PARSE.TARGET_GUESSING")
    anchor = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION")
    assert cast(dict[str, Any], target_guessing["detail"])["tag"] == "normalize_item_like_target"
    assert cast(dict[str, Any], anchor["detail"])["tag"] == "chapter_scope_from_preamble"
    assert cast(dict[str, Any], anchor["detail"])["scope_confidence"] == "inferred"
    assert cast(dict[str, Any], anchor["detail"])["scope_source"] == "preamble"


def test_compile_fi_keeps_registered_provenance_projection_rows_target_scoped(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "1993/805",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "5",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
                    },
                    {
                        "source_statute": "1993/805",
                        "target_unit_kind": "section",
                        "target_norm": "36",
                        "target_chapter": "5",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    anchors = [
        a for a in _projection_rows(facade)
        if a["kind"] == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"
    ]
    assert len(anchors) == 2
    assert {
        (
            cast(dict[str, Any], a["detail"])["target_unit_kind"],
            cast(dict[str, Any], a["detail"])["target_norm"],
            cast(dict[str, Any], a["detail"])["target_chapter"],
        )
        for a in anchors
    } == {
        ("section", "35", "5"),
        ("section", "36", "5"),
    }
    assert {
        (
            cast(dict[str, Any], a["detail"])["scope_confidence"],
            cast(dict[str, Any], a["detail"])["scope_source"],
        )
        for a in anchors
    } == {
        ("inferred", "preamble"),
    }


def test_compile_fi_extracts_provenance_target_scope_from_flat_compiled_op_scope(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "source_statute": "2004/1313",
                        "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
                        "target_unit_kind": "section",
                        "target_norm": "1",
                        "target_chapter": "5a",
                        "target_paragraph": "",
                        "target_item": "",
                        "target_special": "",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    anchor = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION")
    assert cast(dict[str, Any], anchor["detail"])["tag"] == "chapter_scope_from_preamble"
    assert cast(dict[str, Any], anchor["detail"])["target_unit_kind"] == "section"
    assert cast(dict[str, Any], anchor["detail"])["target_norm"] == "1"
    assert cast(dict[str, Any], anchor["detail"])["target_chapter"] == "5a"
    assert cast(dict[str, Any], anchor["detail"])["scope_confidence"] == "inferred"
    assert cast(dict[str, Any], anchor["detail"])["scope_source"] == "preamble"


def test_compile_fi_surfaces_sparse_leftovers_as_projection_rows(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["sparse_leftovers"] = [
                {
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "unassigned_slots": ["2:2", "3:(unlabeled)"],
                },
                {
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "unassigned_slots": ["2:2", "3:(unlabeled)"],
                },
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    leftover_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "ELAB.SPARSE_PAYLOAD_LEFTOVER"
    ]
    assert len(leftover_projection_rows) == 1
    assert leftover_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], leftover_projection_rows[0]["detail"])["target_norm"] == "35"
    assert cast(dict[str, Any], leftover_projection_rows[0]["detail"])["unassigned_slots"] == ("2:2", "3:(unlabeled)")


def test_compile_fi_surfaces_sparse_slot_bindings_as_projection_rows(
    monkeypatch,
) -> None:
    def fake_replay_xml(
        parent_id: str,
        *,
        mode: str = "legal_pit",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert parent_id == "1990/1295"
        assert mode == "legal_pit"
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
            replay_meta_out["sparse_slot_bindings"] = [
                {
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "op_description": "REPLACE 35 § 2 mom",
                    "op_type": "REPLACE",
                    "target_paragraph": 2,
                    "target_item": "",
                    "target_special": "",
                    "payload_slot_index": 1,
                    "payload_slot_label": "2",
                },
                {
                    "source_statute": "1993/805",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "op_description": "REPLACE 35 § 2 mom",
                    "op_type": "REPLACE",
                    "target_paragraph": 2,
                    "target_item": "",
                    "target_special": "",
                    "payload_slot_index": 1,
                    "payload_slot_label": "2",
                },
            ]
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    binding_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "ELAB.SPARSE_SLOT_BINDING"
    ]
    assert len(binding_projection_rows) == 1
    assert binding_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], binding_projection_rows[0]["detail"])["target_norm"] == "35"
    assert cast(dict[str, Any], binding_projection_rows[0]["detail"])["op_description"] == "REPLACE 35 § 2 mom"
    assert cast(dict[str, Any], binding_projection_rows[0]["detail"])["payload_slot_index"] == 1
    assert cast(dict[str, Any], binding_projection_rows[0]["detail"])["payload_slot_label"] == "2"


def test_replay_xml_exposes_fold_and_materialized_state(
    replay_and_facade_2009_953_legal_pit_quirks: tuple[ReplayResult, Any],
) -> None:
    replay = replay_and_facade_2009_953_legal_pit_quirks[0]

    assert replay.replay_fold_state is not None
    assert replay.materialized_state is replay.state


def test_replay_xml_exposes_replay_time_projection_rows_without_explicit_sink() -> None:
    replay = pinned_replay("1991/1707", mode="legal_pit", quiet=True)

    assert "adjudications" not in replay.__dict__
    contingent_sources = sorted({
        str(row.get("source") or "")
        for row in replay.projection_rows()
        if row.get("kind") == "TIME.CONTINGENT_EFFECTIVE_DATE" and row.get("source")
    })

    assert contingent_sources == [
        "1999/1301",
        "2000/922",
        "2001/1349",
        "2004/542",
        "2005/544",
        "2006/1322",
    ]
    override_lifecycle = [
        event
        for event in replay.products.effect_lifecycle_events
        if event.kind == "change_effect_expiry"
        and event.relation is not None
        and event.relation.kind == "extends_effect_expiry"
        and event.effect is not None
        and event.effect.source_instrument.instrument_id == "2006/1322"
        and event.effect.target_address is not None
        and ("section", "4") in event.effect.target_address.path
    ]
    assert override_lifecycle
    assert {event.expires for event in override_lifecycle} == {"2009-12-31"}
    assert {event.executable for event in override_lifecycle} == {True}


def test_replay_xml_1970_258_folds_base_item_subsection_run_before_renumber_insert() -> None:
    replay = pinned_replay("1970/258", oracle_version="20041297", mode="official_consolidation", quiet=True)
    section12 = extract_ir_sections(replay.materialized_state.ir)["section:12"]
    text = " ".join(irnode_to_text(section12).split())

    edunsaaja = "Edunsaajalla, jolle on 4 momentissa tarkoitetun kuolemantapauksen"
    vanha_kuudes = "Jos henkilö tämän lain voimaan tullessa on edunjättäjä"
    inserted = "Milloin 6 momentissa tarkoitettu henkilö kuolee"
    final_tail = "Seurakunnan vastuulle 4 ja 6 momentin mukaan jäävän perhe-eläkkeen"

    assert text.index(edunsaaja) < text.index(vanha_kuudes)
    assert text.index(vanha_kuudes) < text.index(inserted)
    assert text.index(inserted) < text.index(final_tail)


def test_replay_xml_1994_1505_materializes_sparse_definition_item_payloads() -> None:
    replay = pinned_replay("1994/1505", oracle_version="20090774", mode="official_consolidation", quiet=True)
    section3 = extract_ir_sections(replay.materialized_state.ir)["chapter:1/section:3"]
    text = " ".join(irnode_to_text(section3).split())

    assert "1 a) In vitro -diagnostiikkaan tarkoitetulla" in text
    assert "5) Markkinoille saattamisella tarkoitetaan terveydenhuollon laitteen" in text
    assert "6) Käyttöönottamisella tarkoitetaan vaihetta, jolloin" in text
    assert "7) Valtuutetulla edustajalla tarkoitetaan" in text
    assert not any(
        finding.kind == "COVERAGE.PAYLOAD_REALIZATION_GAP"
        and finding.source_statute == "2000/345"
        for finding in replay.findings
    )


def test_inspect_amendment_1994_1505_binds_explicit_1a_sparse_item_slot() -> None:
    bundle = build_amendment_bundle("1994/1505", "2000/345", "legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "3")

    bindings = {
        binding["op"]: binding
        for binding in group["sparse_slot_bindings"]
    }
    assert bindings["INSERT 1 luku 3 § 1 mom 1a kohta"]["slot_label"] == "1a"
    assert bindings["INSERT 1 luku 3 § 1 mom 1a kohta"]["slot_index"] == 1
    assert not any(
        observation["kind"] == "ELAB.UNASSIGNED_SPARSE_SLOTS"
        for observation in group["elaboration_observations"]
    )


def test_inspect_amendment_1997_396_keeps_explicit_45_subsection_shell() -> None:
    bundle = build_amendment_bundle("1997/396", "2001/1119", "legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "45")

    assert group["ops_raw"] == ["REPLACE 10 luku 45 § 1 mom"]
    assert group["ops_after_normalization"] == ["REPLACE 10 luku 45 § 1 mom"]
    assert group["ops_final"] == ["REPLACE 10 luku 45 § 1 mom"]
    assert not any(
        pathology["code"] == "PARTIAL_WHOLE_SECTION_PAYLOAD"
        for pathology in group["source_pathologies"]
    )
    assert any(
        pathology["code"] == "SUBSECTION_SHELL_REPLACE_KEPT"
        for pathology in group["source_pathologies"]
    )


def test_replay_xml_2014_610_splits_2023_tail_moments_before_2026_renumber() -> None:
    replay = pinned_replay("2014/610", oracle_version="20260352", mode="official_consolidation", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["part:4/chapter:15/section:11"]
    text = " ".join(irnode_to_text(section).split())

    first_tail = "Tässä momentissa tarkoitettuna vakuutena ei pidetä henkilötakausta"
    new_third = "Finanssivalvonta voi asuntomarkkinoiden laskusuhdanteen"
    new_fourth = "Finanssivalvonnan on vähintään vuosittain tehtävä päätös"
    moved_fifth = "Päätös, jolla tässä pykälässä tarkoitettua luoton enimmäismäärää alennetaan"
    moved_sixth = "Finanssivalvonta voi antaa määräyksiä tässä pykälässä"

    assert "edellä 2 momentissa säädettyjä luoton enimmäismääriä" not in text
    assert text.count(moved_sixth) == 1
    assert text.index(first_tail) < text.index(new_third)
    assert text.index(new_third) < text.index(new_fourth)
    assert text.index(new_fourth) < text.index(moved_fifth)
    assert text.index(moved_fifth) < text.index(moved_sixth)


def test_replay_xml_2009_273_splits_2015_section_10_tail_moments_before_later_replaces() -> None:
    replay = pinned_replay("2009/273", oracle_version="20251076", mode="official_consolidation", quiet=True)
    section = extract_ir_sections(replay.replay_fold_state.ir)["section:10"]
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    text = " ".join(irnode_to_text(section).split())

    old_penalty = "Uhkasakon tuomitsee valtiontalouden tarkastusvirastosta annetun lain (676/2000) 15 §:ssä"
    new_penalty = "15 a §:ssä tarkoitettu seuraamuslautakunta"
    tail = "Valtiontalouden tarkastusviraston suorittama valvonta päättyy"

    assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
    assert old_penalty not in text
    assert text.count(new_penalty) == 1
    assert text.index(new_penalty) < text.index(tail)


def test_replay_xml_1974_16_keeps_sparse_override_without_prior_law_tail_repair() -> None:
    """Current replay keeps the sparse override text instead of inferring prior-law tail repair."""
    replay = pinned_replay("1974/16", mode="official_consolidation", quiet=True)
    replay_text = replay.serialize_text()

    assert "vähintään yhden hehtaarin peltoa käsittävällä tilalla" not in replay_text
    assert "vähintään kaksi hehtaaria peltoa käsittävällä tilalla" in replay_text


def test_replay_xml_1919_1_scopes_flat_replaces_into_live_chapter_gaps() -> None:
    """Flat section REPLACE payloads fill chapter gaps instead of root sections."""
    replay = pinned_replay("1919/1-001", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:1/section:1" in sections
    assert "chapter:2/section:11" in sections
    assert "chapter:4/section:21" in sections
    assert "chapter:6/section:37" in sections
    assert "chapter:6/section:38" in sections
    assert "chapter:7/section:41" in sections
    assert "chapter:7/section:42" in sections
    assert "chapter:7/section:44" in sections

    top_level_sections = {
        child.label
        for child in replay.materialized_state.ir.children
        if child.kind is IRNodeKind.SECTION
    }
    assert not (top_level_sections & {"1", "11", "21", "37", "38", "41", "42", "44"})


def test_replay_xml_1996_79_preserves_synthesized_chapter_descendant_order() -> None:
    """Active descendant chapter synthesis must keep children under the chapter."""
    replay = pinned_replay("1996/79", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:6a/section:23a" in sections
    assert "chapter:6a/section:23b" in sections

    chapter_6a = next(
        child
        for child in replay.materialized_state.ir.children
        if child.kind is IRNodeKind.CHAPTER and child.label == "6a"
    )
    chapter_6a_section_labels = [
        child.label
        for child in chapter_6a.children
        if child.kind is IRNodeKind.SECTION
    ]
    assert chapter_6a_section_labels == ["23a", "23b"]

    hcontainer_section_labels = [
        grandchild.label
        for child in replay.materialized_state.ir.children
        if child.kind is IRNodeKind.HCONTAINER
        for grandchild in child.children
        if grandchild.kind is IRNodeKind.SECTION
    ]
    assert "23a" not in hcontainer_section_labels
    assert "23b" not in hcontainer_section_labels

    replay_text = replay.serialize_text()
    assert replay_text.index("23 a §") < replay_text.index("7 luku")


def test_replay_xml_1966_611_applies_heading_tagged_subsection_payload() -> None:
    replay = pinned_replay("1966/611", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section4 = sections["section:4"]
    subsection1 = next(
        child
        for child in section4.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    text = " ".join(irnode_to_text(subsection1).split())

    assert subsection1.attrs["lawvm_payload_normalization_rule"] == (
        "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD",
    )
    assert "kihlakunnantuomarin virka B 4" in text
    assert "ulosottoapulaisen toimi V 18" in text
    assert "henkikirjoittajan" not in text


def test_replay_xml_1966_611_preserves_section_5_items_after_sparse_table_row_replace() -> None:
    replay = replay_xml(
        "1966/611",
        mode="legal_pit",
        stop_before="1991/234",
        quiet=True,
        build_full_products=False,
    )
    sections = extract_ir_sections(replay.state.ir)
    section5 = sections["section:5"]
    subsection1 = next(
        child
        for child in section5.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )

    labels = [
        child.label
        for child in subsection1.children
        if child.kind is IRNodeKind.PARAGRAPH
    ]
    text = " ".join(irnode_to_text(subsection1).split())

    assert "7" in labels
    assert "12" in labels
    assert "13" in labels
    assert "poliisissa palvelevana rikosylikonstaapelina" in text
    assert "taikka naiskonstaapelina" not in text
    assert "johtajana, asuntolanjohtajana tai opettajana kuulovammaisten ammattikoulussa" in text
    assert (
        "rehtorina, apulaisrehtorina, oppilaskodinjohtajana tai opettajana "
        "kuulovammaisten tai näkövammaisten koulussa"
    ) in text


def test_compile_fi_1997_786_combines_split_preamble_body_lead_formula() -> None:
    facade = compile_fi_facade("1997/786", replay_mode="official_consolidation")

    section9_ops = [
        op
        for op in facade.bundle.structural_ops
        if op.action is StructuralAction.REPLACE
        and str(op.target) == "section:9"
        and op.source is not None
        and op.source.statute_id == "1999/638"
    ]
    assert len(section9_ops) == 1
    assert section9_ops[0].source is not None
    raw_text = " ".join(section9_ops[0].source.raw_text.split())
    assert "kumotaan yritystuen yleisistä ehdoista" in raw_text
    assert "muutetaan 9 § seuraavasti" in raw_text


@pytest.mark.slow
def test_compile_fi_2009_1698_keeps_source_body_chapter_for_56a_insert() -> None:
    facade = compile_fi_facade("2009/1698", replay_mode="legal_pit")

    inserts = [
        op
        for op in facade.bundle.structural_ops
        if op.action is StructuralAction.INSERT
        and str(op.target) == "chapter:12/section:56a"
        and op.source is not None
        and op.source.statute_id == "2018/1120"
    ]

    assert len(inserts) == 1


@pytest.mark.slow
def test_compile_fi_1993_1501_keeps_source_body_chapter_for_209b_replace() -> None:
    facade = compile_fi_facade("1993/1501", replay_mode="legal_pit")

    replacements = [
        op
        for op in facade.bundle.structural_ops
        if op.action is StructuralAction.REPLACE
        and str(op.target).endswith("chapter:22/section:209b")
        and op.source is not None
        and op.source.statute_id == "2012/399"
    ]

    assert len(replacements) == 1


def test_normalize_and_compile_ops_2007_473_repairs_split_muutetaan_verb() -> None:
    before = replay_xml("1984/603", stop_before="2007/473", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2007/473")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    assert "muute" in johto and "taan" in johto
    from lawvm.finland.metadata import _normalize_johtolause_verbs

    normalized_johto = _normalize_johtolause_verbs(johto)
    assert "muutetaan" in normalized_johto

    phase = normalize_and_compile_ops(
        johto=normalized_johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2007/473",
        source_title="Laki työttömyyskassalain 16 §:n muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1984/603",
        strict_profile=None,
    )

    assert [op.description() for op in phase.output] == ["REPLACE 5 luku 16 §"]


def test_replay_xml_1991_248_heading_only_op_does_not_smuggle_commencement_body() -> None:
    replay = replay_xml("1991/248", stop_before="1996/323", mode="legal_pit", quiet=True, build_full_products=False)
    sections = extract_ir_sections(replay.state.ir)
    sec = sections["chapter:2a/section:27a"]
    text = " ".join(irnode_to_text(sec).split())

    assert "alueiden tasapainoiseen kehittämiseen" in text
    assert "Tämä asetus tulee voimaan 24 päivänä toukokuuta 1996" not in text


def test_replay_xml_1996_1200_merges_sparse_omission_item_rows_in_targeted_subsection() -> None:
    replay = pinned_replay("1996/1200", mode="official_consolidation", quiet=True)
    section9 = extract_ir_sections(replay.materialized_state.ir)["section:9"]
    text = " ".join(irnode_to_text(section9).split())

    assert "5) hakkuun metsälain 5 §:n 1 momentin" in text
    assert "6) uudistushakkuussa metsiköittäin" in text
    assert "pääasiallinen puulaji sekä maanpinnan käsittelymenetelmä;" in text
    assert "pääasiallinen puulaji, maanpinnan käsittelymenetelmä sekä taimikon" not in text
    assert "7) jos metsätalousmaata otetaan metsälain 3 §:ssä" in text
    assert "8) onko kysymyksessä metsälain 12 §:ssä" in text
    assert "9) jos metsän käsittely koskee metsälain 10 §:n" in text
    assert "10) metsänkäyttöilmoituksen laatijan nimi ja yhteystiedot." in text


def test_replay_xml_1994_357_normalizes_colon_intro_content_pairs_before_insert() -> None:
    replay = pinned_replay("1994/357", mode="official_consolidation", quiet=True)
    section2 = extract_ir_sections(replay.materialized_state.ir)["section:2"]
    text = " ".join(irnode_to_text(section2).split())

    first_intro = "JHTT-tutkinto sisältää kirjanpitovelvollisia"
    first_items = "tilintarkastus, tilinpäätösanalyysi, yleinen laskentatoimi"
    second_intro = "JHTT-tutkinto sisältää lisäksi seuraavat oppiaineet:"
    second_items = "julkisyhteisöjen suunnittelujärjestelmä ja budjetointi"
    ec_sentence = "Lisäksi JHTT-tutkinnon vaatimuksiin kuuluu Euroopan yhteisöjen"

    assert text.index(first_intro) < text.index(first_items)
    assert text.index(first_items) < text.index(second_intro)
    assert text.index(second_intro) < text.index(second_items)
    assert text.index(second_items) < text.index(ec_sentence)


def test_replay_xml_1982_1112_splits_first_moment_exception_tail_before_replacement() -> None:
    replay = pinned_replay("1982/1112", mode="official_consolidation", quiet=True)
    section2 = extract_ir_sections(replay.materialized_state.ir)["section:2"]
    text = " ".join(irnode_to_text(section2).split())

    old_tail = "Mitä 1 momentissa on sanottu, ei koske maa- ja metsätalousministeriön luvalla"
    new_tail = "Mitä 1 momentissa säädetään, ei koske Suomen ympäristökeskuksen luvalla"

    assert old_tail not in text
    assert new_tail in text
    assert text.count("Mitä 1 momentissa") == 1


def test_replay_xml_1993_1709_preserves_list_prefix_when_replacing_later_list() -> None:
    replay = pinned_replay("1993/1709", mode="official_consolidation", quiet=True)
    section1 = extract_ir_sections(replay.materialized_state.ir)["section:1"]
    text = " ".join(irnode_to_text(section1).split())

    assert text.index("Vuoden 1961 huumausaineyleissopimuksen luettelo I") < text.index(
        "Psykotrooppisia aineita koskevan yleissopimuksen luettelo I"
    )
    assert text.index("Psykotrooppisia aineita koskevan yleissopimuksen luettelo I") < text.index(
        "Dietyylitryptamiini (DET)"
    )
    assert text.index("Difenoksylaatti") < text.index("Dihydroetorfiini")
    assert text.index("Dihydroetorfiini") < text.index("Dihydromorfiini")
    assert text.index("Rasemorfaani") < text.index("Remifentaniili")
    assert text.index("Remifentaniili") < text.index("Sufentaniili")
    assert "Dietyylitryptamiini (DET)" in text
    assert "Tetrahydrokannabinoli" in text
    subsection1 = next(
        child
        for child in section1.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    assert "ELAB.LEADING_OMISSION_ANCHOR_PREFIX_MERGE" in subsection1.attrs["lawvm_payload_normalization_rule"]


def test_replay_xml_2021_1289_applies_explicit_heading_and_first_moment_replace() -> None:
    replay = pinned_replay("2021/1289", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section8 = sections["chapter:3/section:8"]
    text = " ".join(irnode_to_text(section8).split())

    assert "Sähkö- ja vetykäyttöisen pakettiauton hankintatuen määrä" in text
    assert "sähköä, vetyä tai niiden yhdistelmää käyttövoimana" in text
    assert "akseliväli on enintään 3,5 metriä" in text
    assert "pienikokoinen pakettiauto" not in text


def test_replay_xml_2013_1201_carries_renumbered_section_12_moments() -> None:
    replay = replay_xml("2013/1201", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section12 = sections["chapter:3/section:12"]
    subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section12.children
        if child.kind is IRNodeKind.SUBSECTION
    }

    assert "4" in subsections
    assert "5" in subsections
    assert "Kansaneläkelaitos pyytää edellä 2 ja 3 momentissa mainitut tiedot" in subsections["4"]
    assert "Kirjallinen vastaus on toimitettava pyynnön vastaanottamista seuraavien" in subsections["5"]


def test_replay_xml_1983_361_moves_current_section_21_to_chapter_7_section_50() -> None:
    replay = replay_xml(
        "1983/361",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )
    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:7/section:50" in sections
    assert "chapter:4/section:50" not in sections

    section21_text = " ".join(irnode_to_text(sections["chapter:4/section:21"]).split())
    section50_text = " ".join(irnode_to_text(sections["chapter:7/section:50"]).split())

    assert "Väliaikaisesta määräyksestä" in section21_text
    assert "Tämä laki tulee voimaan 1 päivänä tammikuuta 1984" not in section21_text
    assert "Tämä laki tulee voimaan 1 päivänä tammikuuta 1984" in section50_text


def test_normalize_and_compile_ops_2021_1289_rehomes_reinstatement_list_to_prior_addresses() -> None:
    before = replay_xml("2021/1289", stop_before="2024/420", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2024/420")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2024/420",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="2021/1289",
        strict_profile=None,
    )

    reinstated = {
        op.target_cols.target_section: op
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_section in {"6", "7", "16", "17"}
    }

    assert {label: op.description() for label, op in reinstated.items()} == {
        "6": "INSERT 2 luku 6 §",
        "7": "INSERT 3 luku 7 §",
        "16": "INSERT 4 luku 16 §",
        "17": "INSERT 4 luku 17 §",
    }
    assert {
        op.lo.witness_rule_id
        for op in reinstated.values()
        if op.lo is not None
    } == {"fi_reinstated_section_scope_from_prior_repeal_address"}


@pytest.mark.slow
def test_normalize_and_compile_ops_1734_4_keeps_chapter_scoped_reinstatement_in_source_chapter() -> None:
    before = replay_xml("1734/4-000", stop_before="2025/142", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2025/142")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2025/142",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="1734/4-000",
        strict_profile=None,
    )

    section_17 = next(
        op
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_section == "17" and op.target_cols.target_unit_kind == "section"
    )

    assert section_17.description() == "INSERT 21 luku 17 §"
    assert section_17.lo is None or section_17.lo.witness_rule_id != "fi_reinstated_section_scope_from_prior_repeal_address"


def test_normalize_and_compile_ops_1993_1054_keeps_lisataan_chapter_scoped_reinstatement_in_source_chapter() -> None:
    before = replay_xml("1993/1054", stop_before="2021/200", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2021/200")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2021/200",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="1993/1054",
        strict_profile=None,
    )

    section_3 = next(
        op
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_section == "3" and op.target_cols.target_unit_kind == "section"
    )

    assert section_3.description() == "INSERT 7 luku 3 §"
    assert section_3.lo is None or section_3.lo.witness_rule_id != "fi_reinstated_section_scope_from_prior_repeal_address"


def test_normalize_and_compile_ops_2016_1227_scopes_flat_79_replace_from_siblings() -> None:
    before = replay_xml("2016/1227", stop_before="2022/1149", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2022/1149")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2022/1149",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="2016/1227",
        strict_profile=None,
    )

    section_79_ops = [
        op
        for op in phase.output
        if op.op_type == "REPLACE"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "79"
    ]

    assert [op.description() for op in section_79_ops] == ["REPLACE 8 luku 79 § 1 mom"]
    assert section_79_ops[0].witness_rule_id == "fi_flat_body_replace_scope_from_bracketing_live_siblings"
    assert section_79_ops[0].lo is not None
    assert tuple(section_79_ops[0].lo.target.path) == (
        ("chapter", "8"),
        ("section", "79"),
        ("subsection", "1"),
    )


def test_normalize_and_compile_ops_2004_485_scopes_flat_20a_replace_from_siblings() -> None:
    before = replay_xml("2004/485", stop_before="2018/955", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("2018/955")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2018/955",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="2004/485",
        strict_profile=None,
    )

    section_20a_ops = [
        op
        for op in phase.output
        if op.op_type == "REPLACE"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "20a"
    ]

    assert [op.description() for op in section_20a_ops] == ["REPLACE 4 luku 20a §"]
    assert section_20a_ops[0].lo is not None
    assert tuple(section_20a_ops[0].lo.target.path) == (
        ("chapter", "4"),
        ("section", "20a"),
    )


def test_replay_xml_2004_485_applies_2025_314_item_intro_and_subparagraph_payload() -> None:
    replay = replay_xml("2004/485", mode="official_consolidation", quiet=True, build_full_products=False)
    section = replay.materialized_state.find_section("4", "2")
    assert section is not None
    subsection = next(c for c in section.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    paragraph = next(c for c in subsection.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "1")
    intro = next(c for c in paragraph.children if c.kind in {IRNodeKind.INTRO, IRNodeKind.CONTENT})
    subparagraph_a = next(c for c in paragraph.children if c.kind == IRNodeKind.SUBPARAGRAPH and c.label == "a")

    assert "suorittaa satamarakenteiden ja satamien turva-arvioinnit" in irnode_to_text(intro)
    assert "Liikenne- ja viestintävirasto toimii turvatoimiasetuksen" not in irnode_to_text(intro)
    assert "7 g §:ssä tarkoitetuille henkilöille" in irnode_to_text(subparagraph_a)


def test_replay_xml_2012_999_prunes_stale_item_tail_when_2018_207_supplies_new_tail() -> None:
    replay = replay_xml("2012/999", mode="official_consolidation", quiet=True, build_full_products=False)
    section = replay.materialized_state.find_section("87", "12")
    assert section is not None

    section_text = irnode_to_text(section)
    assert "jollei teosta muualla laissa säädetä ankarampaa rangaistusta" not in section_text
    assert section_text.count("jollei siitä muualla laissa säädetä ankarampaa rangaistusta") == 1

    subsection = next(c for c in section.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    item_4 = next(c for c in subsection.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "4")
    assert not any(child.kind == IRNodeKind.SUBPARAGRAPH for child in item_4.children)


def test_replay_xml_2011_872_keeps_new_5a_sections_shadowed_by_chapter_5_replaces() -> None:
    replay = replay_xml("2011/872", mode="official_consolidation", quiet=True, build_full_products=False)
    sections = extract_ir_sections(replay.materialized_state.ir)

    chapter_5_section_44 = sections["chapter:5/section:44"]
    chapter_5a_section_44 = sections["chapter:5a/section:44"]

    assert "Valvotusta läpilaskusta päättäminen" in irnode_to_text(chapter_5_section_44)
    assert "Rikosepäilystä ilmoittaminen" in irnode_to_text(chapter_5a_section_44)
    assert "Rikosepäilystä ilmoittaminen" not in irnode_to_text(chapter_5_section_44)


def test_normalize_and_compile_ops_1979_1062_keeps_bare_lukuun_reinstatement_local() -> None:
    before = replay_xml("1979/1062", stop_before="1997/611", mode="legal_pit", quiet=True, build_full_products=False)
    corpus = get_corpus_store()
    xml = corpus.read_source("1997/611")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="1997/611",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="1979/1062",
        strict_profile=None,
    )

    inserted_sections = {
        op.description()
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_unit_kind == "section" and op.target_cols.target_section in {"12", "13", "14"}
    }

    assert {
        "INSERT 2 luku 13 §",
        "INSERT 10 luku 12 §",
        "INSERT 10 luku 13 §",
        "INSERT 10 luku 14 §",
    }.issubset(inserted_sections)
    assert all(
        op.lo is None or op.lo.witness_rule_id != "fi_reinstated_section_scope_from_prior_repeal_address"
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_unit_kind == "section" and op.target_cols.target_section in {"12", "13", "14"}
    )


def test_compile_amendment_ops_2004_1287_keeps_live_stem_inserts_in_chapter_2() -> None:
    from lawvm.finland.source_model import AmendmentSourceModel

    before = replay_xml(
        "2004/1287",
        stop_before="2018/1359",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )
    corpus = get_corpus_store()
    xml = corpus.read_source("2018/1359")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    source_model = AmendmentSourceModel.from_tree(muutos_tree, source_ref="2018/1359")
    johto = get_johtolause(xml)

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        base_ir=before.ctx.base_ir,
        amendment_id="2018/1359",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="2004/1287",
        strict_profile=None,
        source_model=source_model,
    )
    target_labels = {"14a", "14b", "14c", "15a", "15b", "15c", "15d", "15e"}
    normalized_inserts = {
        op.target_cols.target_section: op
        for op in phase.output
        if op.op_type == "INSERT" and op.target_cols.target_unit_kind == "section" and op.target_cols.target_section in target_labels
    }
    assert set(normalized_inserts) == target_labels
    assert {op.target_cols.target_chapter for op in normalized_inserts.values()} == {"2"}

    compiled_rows: list[dict[str, object]] = []
    compile_result = compile_amendment_ops(
        before.state,
        phase.output,
        source_model,
        johto,
        "official_consolidation",
        compiled_ops_out=compiled_rows,
        source_ref="2018/1359",
        target_statute="2004/1287",
    )

    compiled_scope_by_label = {
        str(row["target_norm"]): str(row["target_chapter"])
        for row in compiled_rows
        if row.get("action") == "insert" and row.get("target_norm") in target_labels
    }
    assert compiled_scope_by_label == {label: "2" for label in target_labels}
    assert {
        rop.resolved_target_label: rop.resolved_target_scope_chapter_label
        for rop in compile_result.output
        if rop.resolved_action_type == "INSERT" and rop.resolved_target_label in target_labels
    } == {label: "2" for label in target_labels}


def test_replay_1929_234_does_not_duplicate_titled_part_heading_sections() -> None:
    replay = pinned_replay(
        "1929/234",
        mode="official_consolidation",
        quiet=True,
    )

    def _section_paths(
        node: IRNode,
        label: str,
        path: tuple[tuple[str, str], ...] = (),
    ) -> list[tuple[tuple[str, str], ...]]:
        found: list[tuple[tuple[str, str], ...]] = []
        if node.kind is IRNodeKind.SECTION and node.label == label:
            found.append(tuple(step for step in path if step[0] != "hcontainer"))
        for child in node.children:
            found.extend(
                _section_paths(
                    child,
                    label,
                    path + ((child.kind.value, child.label or ""),),
                )
            )
        return found

    expected_110 = [(("part", "5"), ("chapter", "1"), ("section", "110"))]
    expected_129 = [(("part", "5"), ("chapter", "4"), ("section", "129"))]

    assert _section_paths(replay.replay_fold_state.ir, "110") == expected_110
    assert _section_paths(replay.replay_fold_state.ir, "129") == expected_129
    assert _section_paths(replay.ir, "110") == expected_110
    assert _section_paths(replay.ir, "129") == expected_129
    assert replay.find_section("129", "4", "5") is not None
    assert replay.find_section("129", "4", "1") is None


def test_replay_xml_matches_current_oracle_order_for_1987_990_section_55_second_moment(
    replay_1987_990_finlex_oracle: ReplayResult,
) -> None:
    replay = replay_1987_990_finlex_oracle
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:8/section:55"]

    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections[:5]] == ["1", "2", "3", "4", "5"]

    first_subsection_labels = [
        child.label for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH
    ]
    assert first_subsection_labels == []

    second_subsection_labels = [
        child.label for child in subsections[1].children if child.kind == IRNodeKind.PARAGRAPH
    ]
    assert second_subsection_labels == ["1", "2", "3", "4", "5", "6", "6a", "7", "8", "9", "10"]


def test_replay_xml_matches_current_oracle_order_for_1987_990_section_3_first_moment(
    replay_1987_990_finlex_oracle: ReplayResult,
) -> None:
    section = extract_ir_sections(replay_1987_990_finlex_oracle.materialized_state.ir)["chapter:1/section:3"]

    subsection_1 = next(
        child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1"
    )
    paragraph_labels = [child.label for child in subsection_1.children if child.kind == IRNodeKind.PARAGRAPH]

    # Finlex sd-cons 1987/990 fin@20250740 chp_1__sec_3__subsec_1 paragraph order:
    # 1, 2, 3, 4, 5, 5a, 5b, 6, 7, 8, 9, 10, 11, 12, 13, 14
    # The earlier truncated expectation (1..8, 13, 14) was a stale snapshot from
    # an intermediate buggy code path that dropped 5a/5b/9/10/11/12.
    assert paragraph_labels[:10] == ["1", "2", "3", "4", "5", "5a", "5b", "6", "7", "8"]
    assert "13" in paragraph_labels
    assert "14" in paragraph_labels


def test_replay_xml_binds_1987_990_section_17_intro_to_second_moment(
    replay_1987_990_finlex_oracle: ReplayResult,
) -> None:
    """1994/1420 replaces 17 § 1 mom and 2 mom johdantokappale separately."""
    section = extract_ir_sections(replay_1987_990_finlex_oracle.materialized_state.ir)["chapter:5/section:17"]
    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    subsection_texts = [" ".join(irnode_to_text(child).split()) for child in subsections]

    assert subsection_texts[0].startswith("Lupa ydinenergian käyttöön voidaan myöntää vain Euroopan unionin")
    assert subsection_texts[1].startswith("Muulle kuin 1 momentissa tarkoitetulle yhteisölle")
    assert subsection_texts[1].count("Lupa ydinenergian käyttöön voidaan myöntää vain Euroopan unionin") == 0


def test_replay_xml_matches_current_oracle_text_for_1987_990_section_73(
    replay_1987_990_finlex_oracle: ReplayResult,
) -> None:
    section = extract_ir_sections(replay_1987_990_finlex_oracle.materialized_state.ir)["chapter:11/section:73"]
    section_text = irnode_to_text(section)

    assert "malminrikastuslaitos" in section_text
    # The current Finlex oracle no longer contains the sentence
    # "Edellä tässä pykälässä tarkoitetun rikoksen tuottama taloudellinen hyöty
    # tuomitaan valtiolle menetetyksi niin kuin siitä on rikoslaissa säädetty."
    # That sentence was in the 1987 enacted base but was amended away in a later
    # consolidation. An earlier code path leaked the enacted-base sentence into
    # the materialized state, and this assertion previously locked in that bug.
    assert (
        "Rikoslain 44 luvun 10 §:n 1 momentin 1 kohdassa tarkoitetun ydinenergian luvatonta käyttöä koskevan "
        "rikoksen johdosta on tuomittava valtiolle menetetyksi"
    ) in section_text


def test_replay_xml_preserves_2010_1020_johdanto_order() -> None:
    replay = pinned_replay("1998/28", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_20_text = irnode_to_text(sections["chapter:3/section:20"])

    assert "Lupaviranomainen voi viran puolesta muuttaa lupapäätöstä, jos:" in section_20_text
    assert "Lupaviranomainen voi viran puolesta peruuttaa luvan, jos:" in section_20_text
    assert section_20_text.index("muuttaa lupapäätöstä, jos:") < section_20_text.index("peruuttaa luvan, jos:")

    section_25_text = irnode_to_text(sections["chapter:4/section:25"])

    assert "Lupapäätökseen lupaviranomaisen on:" in section_25_text
    assert "Lupaviranomainen pitää rekisteriä" in section_25_text
    assert section_25_text.index("Lupapäätökseen lupaviranomaisen on:") < section_25_text.index(
        "Lupaviranomainen pitää rekisteriä"
    )


def test_compile_amendment_ops_leaves_1977_18_sparse_payload_unrepaired_before_lowering() -> None:
    """Sparse tail normalization is not applied as pre-lowering authority here."""
    before = pinned_replay("1974/16", stop_before="1977/18", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("1977/18")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="1977/18",
        source_title="Laki luopumiseläkelain muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output
    sec2_ops = [op for op in ops if op.target_cols.target_section == "2"]

    result = compile_amendment_ops(
        before.state,
        sec2_ops,
        muutos_tree,
        johto,
        "official_consolidation",
        source_ref="1977/18",
        target_statute="1974/16",
    )
    [rop] = result.output
    amend_sub = rop.resolved_amend_sub_ir()
    assert amend_sub is not None

    paragraph_labels = [child.label for child in amend_sub.children if child.kind == IRNodeKind.PARAGRAPH]
    assert paragraph_labels == ["1", "2", "3", "4"]
    assert any(child.kind == IRNodeKind.OMISSION for child in amend_sub.children)
    assert any(child.kind == IRNodeKind.PARAGRAPH and child.label == "1" for child in amend_sub.children)


def test_1986_508_1996_755_body_only_fallback_binds_wrapper_orphan_subsections() -> None:
    """1996/755 publishes source-owned payload as wrapper-level subsection siblings."""
    before = replay_xml(
        "1986/508",
        mode="official_consolidation",
        stop_before="1996/755",
        quiet=True,
        build_full_products=False,
    )
    corpus = get_corpus_store()
    xml = corpus.read_source("1996/755")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="1996/755",
        source_title="Asetus nuorten työntekijäin suojelusta annetun asetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1986/508",
        strict_profile=None,
    )
    targets = {
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in phase2.output
    }
    assert targets == {
        ("REPLACE", "2", 2, "1"),
        ("REPLACE", "2", 2, "6"),
        ("REPLACE", "2", 2, "8"),
        ("REPLACE", "2", 2, "9"),
        ("REPLACE", "2", 2, "10"),
        ("INSERT", "2", 2, "11"),
        ("INSERT", "5", 4, None),
        ("REPLACE", "6", 1, None),
    }
    assert all("extraction_ceremonial_body_only" in op.extraction_provenance_tags for op in phase2.output)

    result = compile_amendment_ops(
        before.state,
        phase2.output,
        muutos_tree,
        johto,
        "official_consolidation",
        source_ref="1996/755",
        target_statute="1986/508",
    )
    findings = result.findings()
    assert {
        f.detail.get("target_norm")
        for f in findings
        if f.kind == "ELAB.WRAPPER_ORPHAN_SUBSECTION_CONTINUATION"
    } == {"2", "5"}
    sparse_slots = {
        (
            f.detail.get("op_description"),
            f.detail.get("payload_slot_label"),
        )
        for f in findings
        if f.kind == "ELAB.SPARSE_SLOT_BINDING"
    }
    assert ("REPLACE 2 § 2 mom 8 kohta", "3") in sparse_slots
    assert ("INSERT 2 § 2 mom 11 kohta", "6") in sparse_slots
    assert ("INSERT 5 § 4 mom", "4") in sparse_slots


def test_1986_508_replay_keeps_1996_755_body_only_amendment_after_precompile_selection() -> None:
    compiled_ops: list[dict[str, object]] = []
    replay_xml(
        "1986/508",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
        compiled_ops_out=compiled_ops,
    )
    rows = [
        (
            row.get("action"),
            row.get("target_norm"),
            row.get("target_paragraph"),
            row.get("target_item"),
        )
        for row in compiled_ops
        if row.get("source_statute") == "1996/755"
    ]

    assert rows == [
        ("replace", "2", "2", "1"),
        ("replace", "2", "2", "6"),
        ("replace", "2", "2", "8"),
        ("replace", "2", "2", "9"),
        ("replace", "2", "2", "10"),
        ("insert", "2", "2", "11"),
        ("insert", "5", "4", ""),
        ("replace", "6", "1", ""),
    ]


def test_act_wide_body_section_replace_formula_uses_body_section_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section eId="sec_3">
                <num>3 §</num>
                <heading>Changed section</heading>
                <hcontainer name="omission"/>
                <subsection>
                  <intro><p>Changed text.</p></intro>
                  <paragraph><content><p>More changed text.</p></content></paragraph>
                  <hcontainer name="omission"/>
                </subsection>
              </section>
            </hcontainer>
          </body>
        </act>
        """
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Old first."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Old second."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Changed text before."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="More changed text"),
                    ),
                ),
            ),
        )
    )

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Muutetaan testiasetus (1/2020), sellaisena kuin se on asetuksessa 2/2021, seuraavasti:",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2026/1",
        source_title="Asetus testiasetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2020/1",
        strict_profile=None,
    )

    assert [(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_unit_kind) for op in phase2.output] == [
        ("REPLACE", "3", 3, "section"),
        ("REPLACE", "3", 4, "section"),
    ]
    assert {op.witness_rule_id for op in phase2.output} == {"fi.act_wide_body_section_replace"}
    assert all("extraction_act_wide_body_section_replace" in op.extraction_provenance_tags for op in phase2.output)
    findings = [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA"
    ]
    assert len(findings) == 2
    assert {finding.detail["target_section"] for finding in findings} == {"3"}
    assert {finding.detail["target_paragraph"] for finding in findings} == {3, 4}

    strict_phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Muutetaan testiasetus (1/2020), sellaisena kuin se on asetuksessa 2/2021, seuraavasti:",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2026/1",
        source_title="Asetus testiasetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2020/1",
        strict_profile=default_finland_strict_profile(),
    )
    assert strict_phase2.output == []
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=strict_phase2.findings(),
    )
    assert "ELAB.STRICT_REJECTED_OPERATION" in reasons


def test_2023_608_2026_159_act_wide_body_section_replace_regression() -> None:
    before = replay_xml(
        "2023/608",
        mode="official_consolidation",
        stop_before="2026/159",
        quiet=True,
        build_full_products=False,
    )
    corpus = get_corpus_store()
    xml = corpus.read_source("2026/159")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        amendment_id="2026/159",
        source_title=(
            "Maa- ja metsätalousministeriön asetus maatalouden investointien "
            "hyväksyttävistä yksikkökustannuksista annetun maa- ja "
            "metsätalousministeriön asetuksen muuttamisesta"
        ),
        used_preamble_body_fallback=False,
        parent_id="2023/608",
        strict_profile=None,
    )

    assert [(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in phase2.output] == [
        ("REPLACE", "3", 3),
        ("REPLACE", "3", 4),
    ]
    assert any(
        finding.kind == "PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA"
        and finding.detail.get("target_section") == "3"
        and finding.detail.get("target_paragraph") == 3
        for finding in phase2.findings()
    )


def test_normalize_and_compile_ops_parses_1980_1037_spaced_pykala_genitive_as_momentti_target() -> None:
    before = pinned_replay("1974/16", stop_before="1980/1037", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("1980/1037")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="1980/1037",
        source_title="Laki luopumiseläkelain muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output
    sec1_ops = [op for op in ops if op.target_cols.target_section == "1"]

    assert [op.target_cols.target_paragraph for op in sec1_ops] == [3]


def test_normalize_and_compile_ops_parses_1979_1032_reinstated_subsection_insert() -> None:
    before = pinned_replay("1974/16", stop_before="1979/1032", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("1979/1032")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="1979/1032",
        source_title="Laki luopumiseläkelain muuttamisesta.",
        used_preamble_body_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output

    sec6_insert_ops = [
        op for op in ops if op.op_type == "INSERT" and op.target_cols.target_section == "6" and op.target_cols.target_paragraph == 4
    ]

    assert len(sec6_insert_ops) == 1


def test_normalize_and_compile_ops_2017_571_keeps_doc_ill_subsection_insert_target() -> None:
    before = pinned_replay("2002/1244", stop_before="2017/571", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("2017/571")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="2017/571",
        source_title="Valtioneuvoston asetus ajoneuvojen hyväksynnästä annetun valtioneuvoston asetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2002/1244",
        strict_profile=None,
    )
    ops = phase2.output
    sec1_insert_ops = [op for op in ops if op.op_type == "INSERT" and op.target_cols.target_section == "1"]

    assert [(op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item) for op in sec1_insert_ops] == [("1", 2, None)]


def test_normalize_and_compile_ops_2018_1330_keeps_late_grouped_insert_targets() -> None:
    before = pinned_replay("2009/1599", stop_before="2018/1330", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("2018/1330")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        mid="2018/1330",
        source_title="Laki asunto-osakeyhtiölain muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2009/1599",
        strict_profile=None,
    )
    ops = phase2.output

    grouped_inserts = {
        (op.target_cols.target_chapter, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in ops
        if op.op_type == "INSERT"
    }

    assert ("7", "27", 2, "12a") in grouped_inserts
    assert ("13", "13", 5, None) in grouped_inserts
    assert ("19", "14", 3, None) in grouped_inserts
    assert ("19", "14", 4, None) in grouped_inserts
    assert ("20", "14", 2, None) in grouped_inserts
    assert ("20", "14", 3, None) in grouped_inserts


def test_replay_2005_966_renests_flat_digit_item_continuation_before_2020_828() -> None:
    """2011/1271 serializes 9:52(2) items 8-9 as malformed sibling moments."""
    before = replay_xml("2005/966", stop_before="2020/828", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(before.materialized_state.ir)
    section_52 = sections["chapter:9/section:52"]
    subsection_2 = next(
        child
        for child in section_52.children
        if child.kind == IRNodeKind.SUBSECTION and child.label == "2"
    )

    labels = [child.label for child in subsection_2.children if child.kind == IRNodeKind.PARAGRAPH]

    assert labels == [str(i) for i in range(1, 10)]


def test_replay_2009_1599_keeps_section_31_heading_despite_2023_280_sparse_payload() -> None:
    replay = pinned_replay("2009/1599", mode="official_consolidation", quiet=True)
    sec31 = replay.state.find_section("31", "6")
    assert sec31 is not None

    heading = next((child for child in sec31.children if child.kind == IRNodeKind.HEADING), None)
    assert heading is not None
    assert heading.text == "Päätös kaikkien osakkeenomistajien rahoittamasta uudistuksesta"


def test_compile_fi_respects_more_permissive_strict_profile(
    replay_compile_inputs_2002_1090_legal_pit: _ReplayCompileInputs,
) -> None:
    facade = _compile_facade_from_inputs(
        replay_compile_inputs_2002_1090_legal_pit,
        compile_mode="quirks",
        strict_profile=StrictProfile(
            name="finland_relaxed_ingestion_v1",
            allows_uncovered_body_recovery=True,
            allows_fallback_whole_section_replace=True,
            allows_omission_expansion=True,
            allows_estimated_dates=True,
            allows_context_dependent_anchor_resolution=True,
            allows_target_guessing=True,
            allows_word_substitution=True,
        ),
    )

    assert facade.verdict is not None
    assert "APPLY.UNCOVERED_BODY_RECOVERY" not in facade.verdict.barrier_codes
    assert "APPLY.FALLBACK_WHOLE_SECTION_REPLACE" not in facade.verdict.barrier_codes
    # The wire artifact still carries unrelated blocking replay findings, so
    # the relaxed profile is only expected to suppress the profile-gated
    # strict verdict reasons.
    wire_status = facade.to_wire_artifact().processing_status
    assert wire_status.kind == "partial"
    assert "APPLY.SOURCE_INCOMPLETE" in tuple(wire_status.blockers or ())


@pytest.mark.parametrize(
    ("body_ops", "title_ops", "ef_ops", "expected_message"),
    [
        (1, 0, 0, "_extract_root_replace_ops_from_body_fallback rejected by strict profile (allows_target_guessing=False)"),
        (0, 1, 0, "parse_ops_title_fallback rejected by strict profile (allows_target_guessing=False)"),
        (0, 0, 1, "_extract_enacting_formula_body_insert_ops_fallback rejected by strict profile (allows_target_guessing=False)"),
    ],
)
def test_normalize_and_compile_ops_strictly_rejects_late_fallback_chains(
    monkeypatch: pytest.MonkeyPatch,
    body_ops: int,
    title_ops: int,
    ef_ops: int,
    expected_message: str,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp

    op = AmendmentOp(
        op_id="fallback-op",
        op_type=OpType.REPLACE,
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
        source_issue_date=cast(Any, None),
    )
    strict_profile = StrictProfile(
        name="strict_no_target_guessing",
        allows_uncovered_body_recovery=False,
        allows_fallback_whole_section_replace=False,
        allows_omission_expansion=False,
        allows_estimated_dates=False,
        allows_context_dependent_anchor_resolution=False,
        allows_target_guessing=False,
        allows_word_substitution=False,
    )
    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    # normalize_and_compile_ops calls extract_johtolause_legal_ops_from_parse_result
    # (the result-based variant), not the older string-based extract_johtolause_legal_ops.
    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(
        frontend_compile,
        "_extract_root_replace_ops_from_body_fallback",
        lambda _johto, _tree: [op] if body_ops else [],
    )
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_title_fallback",
        lambda _title: [op] if title_ops else [],
    )
    monkeypatch.setattr(
        frontend_compile,
        "_extract_enacting_formula_body_insert_ops_fallback",
        lambda _johto, _tree, _master: [op] if ef_ops else [],
    )

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="muutetaan 1 §",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/1",
        source_title="Test title",
        used_preamble_body_fallback=False,
        parent_id="2019/1",
        strict_profile=strict_profile,
    )

    assert phase2.output == []
    matching = [
        finding
        for finding in phase2.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
        and finding.detail.get("message") == expected_message
    ]
    assert matching
    assert matching[0].blocking is True
    rejected_obs = [
        finding
        for finding in phase2.findings()
        if finding.kind == "ELAB.REJECTED_OPERATION"
        and finding.detail.get("message") == expected_message
    ]
    assert rejected_obs
    assert rejected_obs[0].blocking is False


def test_normalize_and_compile_ops_forwards_fallback_regex_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    coverage_rows: list[Any] = []

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="lisätään 5 §:ään kuitenkin uusi 2 momentti",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/1",
        source_title="Test title",
        used_preamble_body_fallback=False,
        parent_id="2019/1",
        strict_profile=None,
        parse_result=cast(Any, SimpleNamespace(findings=())),
        regex_recognition_coverage_out=coverage_rows,
    )

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in phase2.output
    ] == [("INSERT", "5", 2)]
    assert len(coverage_rows) == 1
    coverage = coverage_rows[0].to_dict()
    assert coverage["recognizer_id"] == "fi_insert_subsection_fallback"
    assert coverage["coverage_status"] == "unclassified_gap"
    assert coverage["source_artifact_id"] == "2020/1"


def test_strip_impossible_chapter_scope_for_bare_body_section_op_clears_no_chapter_parent_leak() -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp

    op = AmendmentOp(
        op_id="ins-21",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="1",
        target_paragraph=1,
        target_item="21",
        target_chapter="1",
    )
    muutos_tree = etree.fromstring(
        "<body><section><num>1 §</num><subsection><num>1 mom.</num></subsection></section></body>"
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", children=()),),
        )
    )

    patched = frontend_compile._strip_impossible_chapter_scope_for_bare_body_section_op(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert patched is not None
    assert patched.target_cols.target_chapter is None


def test_strip_impossible_chapter_scope_for_bare_body_section_op_keeps_real_chaptered_parent() -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp

    op = AmendmentOp(
        op_id="ins-21",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="1",
        target_paragraph=1,
        target_item="21",
        target_chapter="1",
    )
    muutos_tree = etree.fromstring(
        "<body><section><num>1 §</num><subsection><num>1 mom.</num></subsection></section></body>"
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=()),),
        )
    )

    patched = frontend_compile._strip_impossible_chapter_scope_for_bare_body_section_op(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert patched is None


def test_duplicate_section_scope_from_source_heading_binds_unique_live_duplicate() -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp
    from lawvm.finland.source_model import AmendmentSourceModel

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="17",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="17 §"),
                                IRNode(kind=IRNodeKind.HEADING, text="Unrelated costs"),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="17",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="17 §"),
                                IRNode(kind=IRNodeKind.HEADING, text="Water administration tasks"),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    source_tree = etree.fromstring(
        """
        <act>
          <body>
            <section>
              <num>17 §</num>
              <heading>Water administration task</heading>
              <subsection><content><p>Payload.</p></content></subsection>
            </section>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace-17",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="17",
    )

    assert frontend_compile._infer_duplicate_section_scope_from_source_heading(
        op=op,
        master=master,
        source_model=AmendmentSourceModel.from_tree(source_tree),
    ) == (None, "5")


def test_normalize_and_compile_ops_1993_1390_1995_64_scopes_duplicate_17_by_heading() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    before = replay_xml_for_test("1993/1390", stop_before="1995/64", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("1995/64")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        amendment_id="1995/64",
        source_title="Asetus jäteasetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1993/1390",
        strict_profile=None,
    )

    section17 = [
        op
        for op in phase2.output
        if op.op_type == "REPLACE"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "17"
        and op.target_cols.target_paragraph is None
    ]
    assert len(section17) == 1
    assert section17[0].target_cols.target_chapter == "5"
    assert section17[0].witness_rule_id == "fi_duplicate_section_scope_from_source_heading"


def test_normalize_and_compile_ops_1996_627_does_not_leak_parent_title_chapter_scope() -> None:
    before = pinned_replay("1996/627", stop_before="2023/674", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("2023/674")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        amendment_id="2023/674",
        source_title="Valtioneuvoston asetus rikoslain 1 luvun 7 §:n soveltamisesta annetun asetuksen 1 §:n muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1996/627",
        strict_profile=None,
    )

    target_ops = [
        op
        for op in phase2.output
        if op.target_cols.target_unit_kind == "section" and op.target_cols.target_section == "1"
    ]
    assert target_ops
    assert all(not op.target_cols.target_chapter for op in target_ops)
    assert any(
        op.op_type == "INSERT"
        and op.target_cols.target_paragraph == 1
        and op.target_cols.target_item == "21"
        and not op.target_cols.target_chapter
        for op in target_ops
    )


def test_normalize_and_compile_ops_1968_360_2019_308_does_not_leak_heading_chapter_scope() -> None:
    before = pinned_replay("1968/360", stop_before="2019/308", mode="official_consolidation", quiet=True)
    corpus = get_corpus_store()
    xml = corpus.read_source("2019/308")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=before.state,
        amendment_id="2019/308",
        source_title="Laki elinkeinotulon verottamisesta annetun lain muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1968/360",
        strict_profile=None,
    )

    inserted_subsections = {
        op.target_cols.target_section: op
        for op in phase2.output
        if op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_paragraph == 2
        and op.target_cols.target_section in {"1", "2", "7"}
    }
    assert inserted_subsections["1"].target_cols.target_chapter is None
    assert inserted_subsections["2"].target_cols.target_chapter is None
    assert inserted_subsections["7"].target_cols.target_chapter == "2"

    scoped_whole_section_inserts = {
        op.target_cols.target_section: op.target_cols.target_chapter
        for op in phase2.output
        if op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_paragraph is None
        and op.target_cols.target_section in {"42a"}
    }
    assert scoped_whole_section_inserts["42a"] == "3"
    assert any(
        op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "53"
        and op.target_cols.target_paragraph == 3
        and op.target_cols.target_chapter == "3"
        for op in phase2.output
    )


def test_normalize_and_compile_ops_2014_120_2017_601_keeps_minus_range_subsection_inserts() -> None:
    corpus = get_corpus_store()
    xml = corpus.read_source("2017/601")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="3", children=()),
                IRNode(kind=IRNodeKind.SECTION, label="4", children=()),
            ),
        )
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2017/601",
        source_title="Valtioneuvoston asetus julkisen talouden suunnitelmasta annetun valtioneuvoston asetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="2014/120",
        strict_profile=None,
    )

    section3_inserts = sorted(
        op.target_cols.target_paragraph
        for op in phase2.output
        if op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "3"
    )
    assert section3_inserts == [8, 9, 10]
    assert any(
        op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == "5a"
        and op.target_cols.target_paragraph is None
        for op in phase2.output
    )


def test_normalize_and_compile_ops_1734_3_1973_390_keeps_chapter_reinsert_sections() -> None:
    corpus = get_corpus_store()
    xml = corpus.read_source("1973/390")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="9", children=()),
                IRNode(kind=IRNodeKind.CHAPTER, label="12", children=()),
            ),
        )
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="1973/390",
        source_title="Laki kauppakaaren muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1734/3-000",
        strict_profile=None,
    )

    assert [
        (op.op_type, op.target_cols.target_unit_kind, op.target_cols.target_chapter, op.target_cols.target_section)
        for op in phase2.output
    ] == [
        ("REPLACE", "chapter", None, "12"),
        ("INSERT", "section", "9", "12"),
        ("INSERT", "section", "9", "13"),
    ]


def test_normalize_and_compile_ops_2011_516_2011_582_keeps_short_operative_preamble() -> None:
    corpus = get_corpus_store()
    xml = corpus.read_source("2011/582")
    assert xml is not None
    muutos_tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
                IRNode(kind=IRNodeKind.SECTION, label="2", children=()),
            ),
        )
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2011/582",
        source_title=(
            "Oikeusministeriön asetus ulosottoperustetta koskevan tuomioistuimen "
            "ilmoitusvelvollisuuden alkamisesta annetun asetuksen muuttamisesta"
        ),
        used_preamble_body_fallback=False,
        parent_id="2011/516",
        strict_profile=None,
    )

    assert [
        (
            op.op_type,
            op.target_cols.target_unit_kind,
            op.target_cols.target_section,
            has_recognizer(op.provenance, RecognizerId.SEC1_BODY_JOHTO),
        )
        for op in phase2.output
    ] == [("REPLACE", "section", "1", False)]
    assert phase2.output[0].source_statute == "2011/582"
    assert phase2.output[0].lo is not None
    assert phase2.output[0].lo.source.raw_text == "muutetaan (516/2011) 1 § seuraavasti:"


def test_normalize_and_compile_ops_records_empty_extraction_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])
    monkeypatch.setattr(
        frontend_compile,
        "_extract_enacting_formula_body_insert_ops_fallback",
        lambda _johto, _tree, _master: [],
    )

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Puuttuu johtolause.",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/1",
        source_title="Test title",
        used_preamble_body_fallback=False,
        parent_id="2019/1",
        strict_profile=None,
    )

    assert phase2.output == []
    matching = [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.EXTRACTION_EMPTY"
    ]
    assert matching
    assert matching[0].detail.get("peg_skip_for_sec1_repeal_list") is False


def test_normalize_and_compile_ops_records_unowned_enacting_formula_body_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section><num>5 §</num><subsection><content><p>existing section body</p></content></subsection></section>
            <section><num>5 a §</num><subsection><content><p>new section body</p></content></subsection></section>
          </body>
        </act>
        """
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
        )
    )

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Eduskunnan päätöksen mukaisesti",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/3",
        source_title="Test title",
        used_preamble_body_fallback=False,
        parent_id="2019/1",
        strict_profile=None,
    )

    assert [op.target_cols.target_section for op in phase2.output] == ["5a"]
    unowned = [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.UNOWNED_BODY_SECTION"
    ]
    assert len(unowned) == 1
    assert unowned[0].detail["reason_code"] == "plain_number_not_owned_by_insert_fallback"
    assert unowned[0].detail["num_text"] == "5 §"
    assert unowned[0].detail["accepted_insert_targets"] == ("5a",)

    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=phase2.findings(),
    )
    assert "PARSE.UNOWNED_BODY_SECTION" in reasons


def test_normalize_and_compile_ops_does_not_record_unowned_body_section_for_fully_owned_enacting_formula_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section><num>5 a §</num><subsection><content><p>new section body</p></content></subsection></section>
          </body>
        </act>
        """
    )
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result, diagnostics_out=None: [])
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Eduskunnan päätöksen mukaisesti",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/4",
        source_title="Test title",
        used_preamble_body_fallback=False,
        parent_id="2019/1",
        strict_profile=None,
    )

    assert [op.target_cols.target_section for op in phase2.output] == ["5a"]
    assert [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.UNOWNED_BODY_SECTION"
    ] == []


def test_normalize_and_compile_ops_records_sec1_peg_skip_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    monkeypatch.setattr(frontend_compile, "_sec1_fallback_peg_skip_required", lambda _johto, _parent_id, **_kwargs: True)
    monkeypatch.setattr(
        frontend_compile,
        "parse_ops_fallback_heuristic_with_coverage",
        lambda _johto, source_artifact_id="": SimpleNamespace(
            ops=[],
            regex_recognition_coverage=(),
        ),
    )
    monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])
    monkeypatch.setattr(
        frontend_compile,
        "_extract_enacting_formula_body_insert_ops_fallback",
        lambda _johto, _tree, _master: [],
    )

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto="Kumotaan 1 §:ssä tarkoitettu luettelo.",
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2020/2",
        source_title="Test title",
        used_preamble_body_fallback=True,
        parent_id="2019/1",
        strict_profile=None,
    )

    assert phase2.output == []
    peg_skip = [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.GRAMMAR_SKIP_PREAMBLE_REPEAL_LIST"
    ]
    assert peg_skip
    assert peg_skip[0].detail.get("used_preamble_body_fallback") is True


def test_normalize_and_compile_ops_keeps_sec1_keeper_act_repeal_list_on_peg_path() -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    johto = (
        "Tällä lailla kumotaan eläintautilailla (441/2013) voimaan jätetyt "
        "kumotun eläintautilain (55/1980) 12 §:n 1 momentin johdantokappale "
        "ja 9 kohta sekä 2-4 momentti, 12 f § ja 15 §:n 5 momentti, "
        "sellaisina kuin ne ovat laissa 303/2006."
    )
    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    phase2 = frontend_compile.normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=master,
        amendment_id="2015/521",
        source_title="Laki kumotun eläintautilain voimaan jätettyjen säännösten kumoamisesta",
        used_preamble_body_fallback=True,
        parent_id="1980/55",
        strict_profile=None,
    )

    assert [
        (
            op.op_type,
            op.target_cols.target_section,
            op.target_cols.target_paragraph,
            op.target_cols.target_item,
            op.target_cols.target_special,
        )
        for op in phase2.output
    ] == [
        ("REPEAL", "12", 1, None, "johd"),
        ("REPEAL", "12", 1, "9", None),
        ("REPEAL", "12", 2, None, None),
        ("REPEAL", "12", 3, None, None),
        ("REPEAL", "12", 4, None, None),
        ("REPEAL", "12f", None, None, None),
        ("REPEAL", "15", 5, None, None),
    ]
    assert [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.GRAMMAR_SKIP_PREAMBLE_REPEAL_LIST"
    ] == []


def test_strict_fail_reasons_detect_source_pathology_findings() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="apply",
                detail={
                    "message": "Broad replace target is paired with a suspiciously partial source body."
                },
                blocking=False,
            )
        ],
    )

    assert "APPLY.SOURCE_PATHOLOGY_DETECTED" not in reasons


def test_strict_fail_reasons_detect_oracle_suspect_findings_as_source_incomplete() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            _runtime_violation(
                "APPLY.SOURCE_INCOMPLETE",
                stage="replay",
                message="oracle_suspect: missing_latest_consolidation",
                detail={"oracle_suspect": "missing_latest_consolidation"},
            )
        ],
    )

    assert "APPLY.SOURCE_INCOMPLETE" in reasons


def test_prefixed_source_codes_classify_as_source_incomplete() -> None:
    profile = default_finland_strict_profile()
    for reasons in (
        ["APPLY.SOURCE_INCOMPLETE"],
        ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
        ["APPLY.SOURCE_CORRECTED_BY_PATCH"],
    ):
        verdict = compute_verdict_from_registry(profile, reasons)
        assert verdict.verdict_status == "source_incomplete"


def test_strict_fail_reasons_detect_contingent_effective_date_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="TIME.CONTINGENT_EFFECTIVE_DATE",
                role="obligation",
                stage="process_muutoslaki",
                detail={"message": "Effective date is contingent or decree-set in voimaantulo text."},
                source_statute="2020/1",
                blocking=True,
            )
        ],
    )

    assert "TIME.CONTINGENT_EFFECTIVE_DATE" in reasons


def test_strict_fail_reasons_from_finding_ledger_detect_governed_estimated_effective_date() -> None:
    profile = replace(default_finland_strict_profile(), allows_estimated_dates=False)

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="TIME.ESTIMATED_EFFECTIVE_DATE",
                role="obligation",
                stage="timeline",
                detail={"message": "Effective date was estimated from source metadata."},
                source_statute="2020/1",
                blocking=True,
            )
        ],
    )

    assert "TIME.ESTIMATED_EFFECTIVE_DATE" in reasons


def test_strict_fail_reasons_detect_tree_invariant_violation_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            _runtime_violation(
                "APPLY.TREE_INVARIANT_VIOLATION",
                stage="apply",
                message="Replay tree invariant violated.",
                detail={"violation": "body/section:4: duplicate subsection:1 (2 times)"},
            )
        ],
    )

    assert "APPLY.TREE_INVARIANT_VIOLATION" in reasons


def test_strict_fail_reasons_detect_replay_product_invariant_violation_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            _runtime_violation(
                "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
                stage="apply",
                message="Replay/materialization product invariant violated.",
                detail={"violation": "materialized_tree:body: duplicate section:1 (2 times)"},
            )
        ],
    )

    assert "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION" in reasons


def test_strict_fail_reasons_detect_apply_boundary_violation_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            _runtime_violation(
                "REPLAY_SKIPPED_OP_MUTATED_TREE",
                stage="apply",
                message="skipped replay op still reported tree mutations",
            )
        ],
    )

    assert "REPLAY_SKIPPED_OP_MUTATED_TREE" in reasons


def test_strict_fail_reasons_detect_unresolved_apply_boundary_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            _runtime_violation(
                "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
                stage="apply",
                message="applied replay op mutated the tree without a resolved target boundary",
            )
        ],
    )

    assert "REPLAY_APPLY_BOUNDARY_UNRESOLVED" in reasons


def test_strict_fail_reasons_ignore_text_duplication_warning_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="text_duplication_warning",
                role="observation",
                stage="materialized",
                detail={"message": "Suspicious duplicated text tract.", "kind": "duplicate_suffix_text"},
                blocking=False,
            )
        ],
    )

    assert reasons == []


def test_strict_fail_reasons_ignore_frontend_elaboration_observation_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                role="observation",
                stage="group_payload_normalization",
                source_statute="1993/805",
                detail={
                    "message": "Frontend elaboration recorded observation: align_sparse_omission_subsections_to_live",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                },
                blocking=False,
            )
        ],
    )

    assert reasons == []


def test_strict_fail_reasons_detect_high_uncovered_body_degraded_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
                role="obligation",
                stage="coverage_analysis",
                source_statute="1978/38",
                detail={
                    "message": "chapter-level INSERT plan has high uncovered body ratio; fallback proceeded with explicit degraded confidence",
                },
                blocking=True,
            )
        ],
    )

    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" in reasons


def test_strict_fail_reasons_ignore_sparse_leftover_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
                role="obligation",
                stage="group_payload_normalization",
                source_statute="1993/805",
                detail={
                    "message": "Frontend elaboration preserved unassigned sparse payload slots.",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "unassigned_slots": ["2:2", "3:(unlabeled)"],
                },
                blocking=False,
            )
        ],
    )

    assert reasons == []


def test_strict_fail_reasons_ignore_frontend_sparse_slot_binding_findings() -> None:
    reasons = strict_fail_reasons_from_finding_ledger(
        default_finland_strict_profile(),
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.SPARSE_SLOT_BINDING",
                role="observation",
                stage="group_payload_normalization",
                source_statute="1993/805",
                detail={
                    "message": "Frontend elaboration recorded sparse slot ownership.",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "",
                    "op_description": "REPLACE 35 § 2 mom",
                    "payload_slot_index": 1,
                    "payload_slot_label": "2",
                },
                blocking=False,
            )
        ],
    )

    assert reasons == []


# ---------------------------------------------------------------------------
# Phase 8: Registry-driven strict policy tests
# ---------------------------------------------------------------------------


def test_every_registry_barrier_kind_has_finding_spec() -> None:
    """Every barrier kind in the registry remains a registered finding spec."""
    missing = []
    for code in _strict_barrier_codes():
        spec = get_finding_spec(code)
        if spec is None or spec.role != "barrier":
            missing.append(code)
    assert missing == [], f"barrier codes missing from FINDING_REGISTRY: {missing}"


def test_registry_enforcement_queries() -> None:
    """strict_fail_codes_by_enforcement returns correct subsets."""
    strict_codes = strict_fail_codes_by_enforcement("strict_fail")
    hard_codes = strict_fail_codes_by_enforcement("hard_fail")
    warn_codes = strict_fail_codes_by_enforcement("warn")
    info_codes = strict_fail_codes_by_enforcement("info")

    # Basic sanity: these should be non-empty and disjoint
    assert len(strict_codes) > 0
    assert len(hard_codes) > 0
    assert len(warn_codes) > 0
    assert strict_codes & hard_codes == set()
    assert strict_codes & warn_codes == set()

    # Known members
    assert "APPLY.FAILED_OPERATION" in strict_codes
    assert "APPLY.TREE_INVARIANT_VIOLATION" in hard_codes
    assert "REPLAY_SKIPPED_OP_MUTATED_TREE" in hard_codes
    assert "REPLAY_FAILED_OP_MUTATED_TREE" in hard_codes
    assert "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION" in hard_codes
    assert "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET" in hard_codes
    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" in strict_codes
    assert "ELAB.SOURCE_PATHOLOGY" in warn_codes
    assert "ELAB.SPARSE_SLOT_BINDING" in info_codes


def test_registry_family_queries() -> None:
    """strict_fail_codes_by_family returns correct subsets."""
    recovery_codes = strict_fail_codes_by_family("recovery")
    violation_codes = strict_fail_codes_by_family("violation")
    source_path_codes = strict_fail_codes_by_family("source_pathology")

    assert "APPLY.UNCOVERED_BODY_RECOVERY" in recovery_codes
    assert "APPLY.TREE_INVARIANT_VIOLATION" in violation_codes
    assert "REPLAY_SKIPPED_OP_MUTATED_TREE" in violation_codes
    assert "REPLAY_FAILED_OP_MUTATED_TREE" in violation_codes
    assert "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION" in violation_codes
    assert "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET" in violation_codes
    assert "APPLY.SOURCE_INCOMPLETE" in source_path_codes


def test_barrier_family_from_registry_agrees_with_registry_projection() -> None:
    """barrier_family_from_registry agrees with the registry-driven projection rules."""
    disagreements = []
    for code in _strict_barrier_codes():
        expected_family = _expected_barrier_family_from_registry(code)
        actual = barrier_family_from_registry(code)
        if actual != expected_family:
            disagreements.append((code, expected_family, actual))
    assert disagreements == [], f"Family disagreements: {disagreements}"


def test_strict_fail_reasons_from_finding_ledger_detect_known_recovery() -> None:
    """Findings-native strictness detects the known recovery stack."""
    profile = default_finland_strict_profile()
    recovered = [
        LegalOperation(
            op_id="uncovered_replace_14",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "14"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="14"),
            source=OperationSource(statute_id="2001/1529", effective="2001-01-01"),
        )
    ]
    failures = [
        CompileFailure(
            source_statute="2001/1529",
            description="REPLACE 14 § 4 mom",
            reason="mom_oor(gap=2)",
            target_unit_kind="section",
            target_section="14",
        )
    ]
    compiled_ops: list[dict[str, Any]] = [{"provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",))}]
    canonical_ops: list[LegalOperation] = recovered
    compile_failures: list[CompileFailure] = failures
    finding_rows: list[Finding] = [
        _runtime_violation(
            "APPLY.UNCOVERED_BODY_RECOVERY",
            stage="apply",
            message="Uncovered-body insertion supplement was used.",
            source_statute="2001/1529",
        ),
        _runtime_violation(
            "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
            stage="apply",
            message="Fallback whole-section replacement was used.",
            source_statute="2001/1529",
        ),
        Finding(
            kind="FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK",
            role="obligation",
            stage="process_muutoslaki",
            detail={
                "message": "Section 1 body text replaced the parsed johtolause before routing.",
                "fallback_stage": "pre_routing",
                "fallback_applied": True,
            },
            source_statute="1993/949",
            blocking=True,
        ),
    ]

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=compiled_ops,
        canonical_ops=canonical_ops,
        failures=compile_failures,
        findings=finding_rows,
    )
    assert "APPLY.FAILED_OPERATION" in reasons
    assert "APPLY.UNCOVERED_BODY_RECOVERY" in reasons
    assert "APPLY.FALLBACK_WHOLE_SECTION_REPLACE" in reasons
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in reasons
    assert "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_structured_scope_confidence() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[
            {"scope_source": "preamble", "scope_confidence": "inferred"},
            {"scope_source": "explicit_chunk", "scope_confidence": "explicit"},
            {"scope_source": "explicit_scope_rewrite", "scope_confidence": "rewritten"},
        ],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in reasons
    assert "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED" in reasons
    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" in reasons


def test_strict_fail_reasons_from_finding_ledger_prefers_structured_scope_witness_over_legacy_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[
            {
                "scope_source": "preamble",
                "scope_confidence": "inferred",
                "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_stripped_unique_section",)),
            },
        ],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in reasons
    assert "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED" not in reasons


def test_strict_fail_reasons_from_finding_ledger_keeps_legacy_scope_fallback_per_row() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[
            {
                "scope_source": "preamble",
                "scope_confidence": "inferred",
            },
            {
                "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_explicit_chunk",)),
                "target_unit_kind": "section",
                "target_section": "14",
                "target_chapter": "5",
            },
        ],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in reasons
    assert "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED" in reasons


def test_strict_fail_reasons_detect_rejected_operation_obligation() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.STRICT_REJECTED_OPERATION",
                role="obligation",
                stage="_elaborate_group",
                detail={
                    "message": "operation rejected before apply",
                    "description": "REPLACE 5 §",
                    "reason": "_c_language_variant: language-variant-only johto",
                    "reason_code": "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY",
                    "target_unit_kind": "section",
                    "target_section": "5",
                    "target_chapter": "",
                },
                source_statute="2020/1",
                blocking=True,
            )
        ],
    )

    assert "ELAB.STRICT_REJECTED_OPERATION" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_typed_target_guessing_provenance_tags() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"provenance": serialized_provenance_from_bags(target_guessing_tags=("normalize_item_like_target",))}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.TARGET_GUESSING" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_legacy_dispatch_fallback_again() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                detail={"message": "Apply fell back to legacy field-based dispatch."},
                blocking=True,
            )
        ],
    )

    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_semantic_collapse_move_renumber_again() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                role="observation",
                stage="frontend_extraction",
                source_statute="2020/1",
                detail={
                    "message": "Frontend elaboration recorded observation: PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                    "target_unit_kind": "section",
                    "target_norm": "33",
                    "target_chapter": "5",
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
                blocking=False,
            )
        ],
    )

    assert "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" in reasons


def test_group_surface_uses_single_mislabeled_body_section_for_explicit_replace() -> None:
    from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
    from lawvm.finland.source_model import AmendmentSourceModel

    muutos_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section>
                <num>57§</num>
                <subsection>
                  <content>
                    <p>Tässä laissa tarkoitetaan pankilla Suomen Pankkia.</p>
                  </content>
                </subsection>
              </section>
            </hcontainer>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="explicit-54",
        op_type=OpType.REPLACE,
        target_section="54",
        target_chapter="11",
        target_unit_kind="section",
        source_statute="1971/304",
        witness_rule_id="fi.section_ref",
    )

    result = build_group_surface(
        BuildGroupSurfaceRequest(
            group_ops=[op],
            target_unit_kind="section",
            target_norm="54",
            target_chapter="11",
            target_part=None,
            source_model=AmendmentSourceModel.from_tree(muutos_tree, source_ref="1971/304"),
        )
    )

    assert result.output.body_ir is not None
    text = irnode_to_text(result.output.body_ir)
    assert text.startswith("54 §")
    assert "Tässä laissa tarkoitetaan pankilla Suomen Pankkia" in text
    pathology = next(f for f in result.findings() if f.kind == "ELAB.SOURCE_PATHOLOGY")
    assert pathology.detail["code"] == "BODY_SECTION_LABEL_MISMATCH_PAYLOAD"
    assert pathology.detail["target_section"] == "54"
    assert pathology.detail["observed_section"] == "57"


def test_group_surface_does_not_reuse_payload_claimed_by_another_section_op() -> None:
    from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
    from lawvm.finland.source_model import AmendmentSourceModel

    muutos_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section>
                <num>6 §</num>
                <heading>Viitemäärä</heading>
                <subsection>
                  <content>
                    <p>Keskimääräisen maitotuotoksen laskennassa käytetään viitemäärää.</p>
                  </content>
                </subsection>
              </section>
            </hcontainer>
          </body>
        </act>
        """
    )
    current = AmendmentOp(
        op_id="explicit-2",
        op_type=OpType.REPLACE,
        target_section="2",
        target_unit_kind="section",
        source_statute="2000/464",
        witness_rule_id="fi.section_ref",
    )
    claimed = AmendmentOp(
        op_id="explicit-6",
        op_type=OpType.REPLACE,
        target_section="6",
        target_unit_kind="section",
        source_statute="2000/464",
        witness_rule_id="fi.section_ref",
    )

    result = build_group_surface(
        BuildGroupSurfaceRequest(
            group_ops=[current],
            amendment_group_ops=(current, claimed),
            target_unit_kind="section",
            target_norm="2",
            target_chapter=None,
            target_part=None,
            source_model=AmendmentSourceModel.from_tree(muutos_tree, source_ref="2000/464"),
        )
    )

    assert result.output.body_ir is None
    assert not any(
        f.kind == "ELAB.SOURCE_PATHOLOGY"
        and f.detail.get("code") == "BODY_SECTION_LABEL_MISMATCH_PAYLOAD"
        for f in result.findings()
    )


def test_replay_xml_1932_244_updates_section_54_after_1971_304_label_mismatch() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    replay = replay_xml_for_test(
        "1932/244",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )
    sec = replay.materialized_state.find_section("54", "11")

    assert sec is not None
    text = irnode_to_text(sec)
    assert "Tässä laissa tarkoitetaan pankilla Suomen Pankkia" in text
    assert "Pankkina pidetään tämän lain mukaan" not in text
    assert any(
        finding.kind == "ELAB.SOURCE_PATHOLOGY"
        and finding.detail.get("code") == "BODY_SECTION_LABEL_MISMATCH_PAYLOAD"
        and finding.source_statute == "1971/304"
        for finding in replay.findings
    )


def test_strict_fail_reasons_from_finding_ledger_detect_source_pathology_again() -> None:
    profile = default_finland_strict_profile()
    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="apply",
                detail={"message": "test"},
                blocking=False,
            )
        ],
    )
    assert "APPLY.SOURCE_PATHOLOGY_DETECTED" not in reasons


def test_strict_fail_reasons_from_finding_ledger_detect_legacy_dispatch_fallback_again() -> None:
    profile = default_finland_strict_profile()
    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="APPLY.LEGACY_DISPATCH_FALLBACK",
                role="obligation",
                stage="apply",
                detail={"message": "Apply fell back to legacy field-based dispatch."},
                blocking=True,
            )
        ],
    )
    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in reasons


def test_strict_fail_reasons_from_finding_ledger_respects_profile_gates() -> None:
    """Findings-native strictness respects profile allowances."""
    relaxed = StrictProfile(
        name="relaxed",
        allows_uncovered_body_recovery=True,
        allows_fallback_whole_section_replace=True,
        allows_omission_expansion=True,
        allows_target_guessing=True,
        allows_context_dependent_anchor_resolution=True,
        allows_word_substitution=True,
    )
    recovered = [
        LegalOperation(
            op_id="uncovered_replace_14",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "14"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="14"),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old"),
                replacement="new",
            ),
            source=OperationSource(statute_id="2001/1529", effective="2001-01-01"),
        )
    ]
    new = strict_fail_reasons_from_finding_ledger(
        relaxed,
        compiled_ops=[{
            "provenance": serialized_provenance_from_bags(
                target_guessing_tags=("normalize_item_like_target",),
                scope_tags=("chapter_scope_from_preamble",),
            ),
        }],
        canonical_ops=recovered,
        failures=[],
        findings=[
            _runtime_violation(
                "APPLY.UNCOVERED_BODY_RECOVERY",
                stage="apply",
                message="Uncovered-body insertion supplement was used.",
                source_statute="2001/1529",
            ),
            _runtime_violation(
                "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
                stage="apply",
                message="Fallback whole-section replacement was used.",
                source_statute="2001/1529",
            ),
            Finding(
                kind="FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK",
                role="obligation",
                stage="process_muutoslaki",
                detail={
                    "message": "Section 1 body text replaced the parsed johtolause before routing.",
                    "fallback_stage": "pre_routing",
                    "fallback_applied": True,
                },
                source_statute="1993/949",
                blocking=True,
            ),
        ],
    )
    # The relaxed profile should suppress recovery/guessing/anchor/word_sub reasons
    assert "APPLY.UNCOVERED_BODY_RECOVERY" not in new
    assert "APPLY.FALLBACK_WHOLE_SECTION_REPLACE" not in new
    assert "PARSE.TARGET_GUESSING" not in new
    assert "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK" not in new
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" not in new
    assert "APPLY.WORD_SUBSTITUTION" not in new


def test_strict_fail_reasons_from_finding_ledger_empty_is_clean() -> None:
    """Findings-native strictness returns empty list for clean compilation."""
    profile = default_finland_strict_profile()
    result = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[],
    )
    assert result == []


def test_strict_fail_reasons_from_finding_ledger_ignores_unknown_non_barrier_finding() -> None:
    """Findings-native strictness ignores a finding kind not in the barrier registry."""
    profile = default_finland_strict_profile()
    # text_duplication_warning is not a registered strict barrier — should be ignored
    result = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[
            Finding(
                kind="text_duplication_warning",
                role="observation",
                stage="replay_fold",
                detail={"message": "test"},
                blocking=False,
            )
        ],
    )
    assert result == []


def test_compute_verdict_from_registry_classifies_all_barrier_kinds() -> None:
    """Registry verdict classifies every governed barrier through one core path."""
    profile = default_finland_strict_profile()

    test_cases: list[tuple[list[str], bool]] = [([], False)]
    for code in _strict_barrier_codes():
        expected_family = _expected_barrier_family_from_registry(code)
        test_cases.append(([code], expected_family == "invariant"))
    source_codes = sorted(strict_fail_codes_by_family("source_pathology"))
    if source_codes:
        test_cases.append((source_codes + ["APPLY.UNCOVERED_BODY_RECOVERY"], False))

    for reasons, has_internal in test_cases:
        verdict = compute_verdict_from_registry(profile, reasons, has_internal_failure=has_internal)
        expected_status = "internal_failure" if has_internal else (
            "strict_clean"
            if not reasons
            else "source_incomplete"
            if any(reason in {
                "APPLY.SOURCE_INCOMPLETE",
                "APPLY.SOURCE_PATHOLOGY_DETECTED",
                "APPLY.SOURCE_CORRECTED_BY_PATCH",
            } for reason in reasons)
            else "strict_blocked_by_recovery"
        )
        assert verdict.verdict_status == expected_status, f"Status mismatch for {reasons}: {verdict.verdict_status}"
        assert list(verdict.barrier_codes) == reasons, f"Kind mismatch for {reasons}"
        expected_families: list[str] = []
        for reason in reasons:
            family = barrier_family_from_registry(reason)
            if family not in expected_families:
                expected_families.append(family)
        assert list(verdict.barrier_families) == expected_families, f"Family mismatch for {reasons}"


def test_compute_verdict_from_registry_uses_registry_descriptions() -> None:
    """Registry verdict uses FindingSpec.description instead of string replacement."""
    profile = default_finland_strict_profile()
    verdict = compute_verdict_from_registry(
        profile, ["APPLY.FAILED_OPERATION"], has_internal_failure=False
    )
    # Should use registry description, not "failed operation"
    spec = get_finding_spec("APPLY.FAILED_OPERATION")
    assert spec is not None
    assert verdict.barrier_messages == (spec.description,)


def test_cached_cited_parse_result_keys_by_diagnostic_statute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cited-scope parse cache reuses only the same diagnostic-statute parse."""
    import lawvm.finland.frontend_compile as frontend_compile

    calls: list[tuple[str, str]] = []

    def fake_parse(text: str, *, statute_id: str = "") -> object:
        calls.append((text, statute_id))
        return SimpleNamespace(text=text, statute_id=statute_id)

    frontend_compile._cached_cited_parse_result.cache_clear()
    monkeypatch.setattr(frontend_compile, "parse_johtolause_clause", fake_parse)
    try:
        first = frontend_compile._cached_cited_parse_result(
            "2018/575",
            "1993/1501",
            "muutetaan 30 b §",
        )
        second = frontend_compile._cached_cited_parse_result(
            "2018/575",
            "1993/1501",
            "muutetaan 30 b §",
        )
        other_statute = frontend_compile._cached_cited_parse_result(
            "2018/575",
            "2018/575",
            "muutetaan 30 b §",
        )
    finally:
        frontend_compile._cached_cited_parse_result.cache_clear()

    assert first is second
    assert other_statute is not first
    assert calls == [
        ("muutetaan 30 b §", "1993/1501"),
        ("muutetaan 30 b §", "2018/575"),
    ]


def test_cited_scope_cache_is_lru_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cited-scope cache evicts old scope maps instead of growing for a whole run."""
    import lawvm.finland.frontend_compile as frontend_compile

    old_cache = frontend_compile._cited_scope_cache.copy()
    old_cap = frontend_compile._CITED_SCOPE_CACHE_MAX
    monkeypatch.setattr(frontend_compile, "_CITED_SCOPE_CACHE_MAX", 2)
    frontend_compile._cited_scope_cache.clear()
    try:
        frontend_compile._store_cited_scope_cache(("parent", "2018/575", 1), {"1": (None, "1")})
        frontend_compile._store_cited_scope_cache(("parent", "2018/576", 1), {"2": (None, "2")})
        frontend_compile._cited_scope_cache.move_to_end(("parent", "2018/575", 1))
        frontend_compile._store_cited_scope_cache(("parent", "2018/577", 1), {"3": (None, "3")})

        assert list(frontend_compile._cited_scope_cache) == [
            ("parent", "2018/575", 1),
            ("parent", "2018/577", 1),
        ]
    finally:
        frontend_compile._cited_scope_cache.clear()
        frontend_compile._cited_scope_cache.update(old_cache)
        monkeypatch.setattr(frontend_compile, "_CITED_SCOPE_CACHE_MAX", old_cap)


def test_cited_effective_date_cache_is_lru_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cited effective-date cache stores misses too, but stays bounded."""
    import lawvm.finland.frontend_compile as frontend_compile

    old_cache = frontend_compile._cited_effective_date_cache.copy()
    old_cap = frontend_compile._CITED_EFFECTIVE_DATE_CACHE_MAX
    monkeypatch.setattr(frontend_compile, "_CITED_EFFECTIVE_DATE_CACHE_MAX", 2)
    frontend_compile._cited_effective_date_cache.clear()
    try:
        frontend_compile._store_cited_effective_date_cache("2018/575", "2020-01-01")
        frontend_compile._store_cited_effective_date_cache("2018/576", None)
        frontend_compile._cited_effective_date_cache.move_to_end("2018/575")
        frontend_compile._store_cited_effective_date_cache("2018/577", "2021-01-01")

        assert list(frontend_compile._cited_effective_date_cache) == [
            "2018/575",
            "2018/577",
        ]
        assert frontend_compile._cited_effective_date_cache["2018/575"] == "2020-01-01"
    finally:
        frontend_compile._cited_effective_date_cache.clear()
        frontend_compile._cited_effective_date_cache.update(old_cache)
        monkeypatch.setattr(frontend_compile, "_CITED_EFFECTIVE_DATE_CACHE_MAX", old_cap)


def test_explicit_payload_fixed_term_prefilter_skips_payload_conversion_without_literal() -> None:
    """A unique source section without 'voimassa' cannot carry fixed-term expiry."""
    import lawvm.finland.frontend_compile as frontend_compile

    class SourceModelWithoutValidityLiteral:
        def unique_section_source_text_contains(
            self,
            section_label: str,
            fragment: str,
            *,
            target_chapter: str | None = None,
            target_part: str | None = None,
        ) -> bool | None:
            assert section_label == "5"
            assert fragment == "voimassa"
            assert target_chapter is None
            assert target_part is None
            return False

        def lookup_section_payload_text(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("payload IR conversion should be skipped")

    op = AmendmentOp(
        op_id="section-without-fixed-term-expiry",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="5",
    )

    assert (
        frontend_compile._explicit_payload_fixed_term_expiry_date(
            op,
            source_model=cast(Any, SourceModelWithoutValidityLiteral()),
        )
        is None
    )


@pytest.mark.slow
def test_replay_xml_2002_1290_does_not_crash_on_registered_item_like_normalization() -> None:
    """Replay should classify 2002/1290 without tripping unregistered payload-normalization findings."""
    result = pinned_replay("2002/1290", mode="official_consolidation", quiet=True, build_full_products=False)
    assert result is not None


@pytest.mark.slow
def test_replay_xml_1995_386_rebuilds_dead_preserved_provision_index() -> None:
    """1999/466 leaves a stale preserved index entry that 2003/444 later probes."""
    result = pinned_replay("1995/386", mode="official_consolidation", quiet=True, build_full_products=False)

    assert result is not None
    assert result.replay_fold_state.find_section_path("25", "6a") is None
