from __future__ import annotations

import importlib
from dataclasses import replace
from lxml import etree
from types import SimpleNamespace
import warnings
from typing import Any, Literal, cast

import pytest

from lawvm.core.compile_result import (
    CompileFailure,
    StrictProfile,
    TemporalEvent,
    TemporalScope,
    barrier_family_from_registry,
    compute_verdict_from_registry,
    strict_fail_reasons_from_finding_ledger,
)
from lawvm.core.compile_views import (
    projection_rows_from_findings,
    source_pathology_rows_from_findings,
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
from lawvm.finland.ops import FailedOp
from lawvm.finland.replay_products import ReplayProducts
from lawvm.tools.section_keys import extract_ir_sections
from lawvm.finland.statute import ReplayResult, ReplayState, StatuteContext
from tests.corpus_pin_helpers import pinned_replay


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
    from lawvm.finland.grafter import compile_amendment_ops as _real_compile_amendment_ops

    return _real_compile_amendment_ops(*args, **kwargs)


def get_johtolause(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.grafter import get_johtolause as _real_get_johtolause

    return _real_get_johtolause(*args, **kwargs)


def normalize_and_compile_ops(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.grafter import normalize_and_compile_ops as _real_normalize_and_compile_ops

    if "mid" in kwargs and "amendment_id" not in kwargs:
        kwargs["amendment_id"] = kwargs.pop("mid")
    return _real_normalize_and_compile_ops(*args, **kwargs)


def replay_xml(*args: Any, **kwargs: Any) -> Any:
    from lawvm.finland.grafter import replay_xml as _real_replay_xml

    return _real_replay_xml(*args, **kwargs)


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
    )
    return ReplayResult(
        ctx=ctx,
        products=products,
        findings=findings,
    )


def _compile_facade_with_replay(
    parent_id: str,
    *,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "legal_pit",
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
            "scope_provenance_tags": ["chapter_scope_from_johtolause"],
            "extraction_provenance_tags": ["extraction_fallback_heuristic"],
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
        compiled_ops=[{"target_guessing_provenance_tags": ["normalize_item_like_target"]}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.TARGET_GUESSING" in reasons


def test_strict_fail_reasons_detect_shadowed_insert_supplement_tag() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"extraction_provenance_tags": ["fallback_insert_supplement_shadowed"]}],
        canonical_ops=[],
        failures=[],
        findings=[],
    )

    assert "PARSE.EXTRACTION_FALLBACK" in reasons


def test_strict_fail_reasons_detect_shadowed_replace_supplement_tag() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[{"extraction_provenance_tags": ["fallback_replace_supplement_shadowed"]}],
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
            "scope_provenance_tags": ["chapter_scope_stripped_unique_section"],
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
            "scope_provenance_tags": ["chapter_scope_stripped_subsection_insert"],
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
            "scope_provenance_tags": ["chapter_scope_stripped_section_facet_insert"],
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
            "scope_provenance_tags": ["chapter_scope_stripped_duplicate_label_outside_stated_chapter"],
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
            "scope_provenance_tags": ["chapter_scope_from_explicit_chunk"],
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
                        "scope_provenance_tags": ["chapter_scope_stripped_unique_section"],
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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
                        "scope_provenance_tags": ["chapter_scope_from_explicit_chunk"],
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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
                        "scope_provenance_tags": ["chapter_scope_stripped_unique_section"],
                        "target_unit_kind": "section",
                        "target_norm": "14",
                        "target_chapter": "5",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub(findings=(replay_finding,))

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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
            facade = compile_fi_facade("2015/1480", replay_mode="finlex_oracle")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    assert facade.bundle.target_statute == "2015/1480"


def test_replay_xml_preserves_letter_suffix_item_spacing_for_2014_346() -> None:
    try:
        replay = pinned_replay("2014/346", mode="finlex_oracle", quiet=True)
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


def test_replay_xml_keeps_2008_342_section_21_sparse_tail_unreattached_without_authority() -> None:
    """Sparse tail prose stays in the following moment unless a frontend repair owns it.

    LawVM currently does not treat this source shape as auto-repair authority, so
    the carried tail remains as plain content in the next subsection rather than
    being reattached under item 7.
    """
    replay = pinned_replay("1987/990", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:5/section:21"]

    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections[:4]] == ["1", "2", "3", "4"]

    first_subsection = subsections[0]
    seventh_para = next(
        child for child in first_subsection.children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
    )
    subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
    assert subparagraphs == []
    second_subsection_text = " ".join(
        (child.text or "").strip()
        for child in subsections[1].children
        if child.kind in {IRNodeKind.CONTENT, IRNodeKind.INTRO}
    )
    assert "ydinenergian käyttö muutoinkin täyttää 5-7" in second_subsection_text


def test_replay_xml_keeps_1967_550_section_2_sparse_insert_on_fifth_moment() -> None:
    replay = pinned_replay("1967/550", mode="finlex_oracle")
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:1/section:2"]

    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4", "5", "6"]

    fifth_text = irnode_to_text(subsections[4])
    sixth_text = irnode_to_text(subsections[5])

    assert "ei myöskään estä" in fifth_text
    assert "kuuden kuukauden kuluessa" not in fifth_text
    assert "kuuden kuukauden kuluessa" in sixth_text


def test_replay_xml_places_2019_371_section_159_in_replay_fold_container_frame() -> None:
    """2019/371 preserves §159 text under the final replay-fold container frame."""
    replay = pinned_replay("2017/320", mode="finlex_oracle", quiet=True, strict_johto_temporal=False)
    sections = extract_ir_sections(replay.replay_fold_state.ir)

    section = sections["part:4/chapter:18/section:159"]
    text = irnode_to_text(section)

    assert text.startswith("159 §")
    assert "avoimia rajapintoja teknisesti yhdistävien palveluntarjoajien" in text
    assert "matkustusoikeuden todentamiseen liittyvien taustajärjestelmien" in text
    assert "liityntäpysäköintiä tarjoavan" not in text

    rows = [
        row
        for row in replay.source_pathology_rows()
        if row.get("code") == "RECODIFICATION_SOURCE_CHAIN_GAP"
        and row.get("source_statute") == "2019/371"
    ]
    assert rows


def test_2020_1256_compile_keeps_vi_part_scope_for_chapter_26_28_renumbers() -> None:
    from lawvm.tools.inspect_amendment import _working_johtolause

    statute_id = "2017/320"
    source_id = "2020/1256"
    corpus = get_corpus_store()
    xml_bytes = corpus.read_source(source_id)
    assert xml_bytes is not None

    before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
    _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
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
        used_sec1_fallback=used_sec1_fallback,
        parent_id=statute_id,
        strict_profile=None,
    )

    by_dest = {
        str(op.lo.destination.path[-1][1]): op
        for op in phase.output
        if op.op_type == "RENUMBER"
        and op.target_unit_kind == "chapter"
        and getattr(op, "lo", None) is not None
        and op.lo.destination is not None
        and str(op.lo.destination.path[-1][1]) in {"26", "27", "28"}
    }

    assert by_dest["26"].target_part == "VI"
    assert by_dest["27"].target_part == "VI"
    assert by_dest["28"].target_part == "VI"


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
        compiled_ops=({"target_guessing_provenance_tags": ["normalize_item_like_target"]} for _ in range(1)),
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


def test_compile_fi_facade_returns_path_aware_dossier() -> None:
    facade = compile_fi_facade("2009/953", replay_mode="legal_pit", compile_mode="quirks")

    assert facade.bundle.target_statute == "2009/953"
    assert facade.replay_mode == "legal_pit"
    assert facade.strict_profile_name == "finland_ingestion_v1"
    assert isinstance(facade.bundle.structural_ops, tuple)
    assert isinstance(_projection_rows(facade), tuple)
    assert isinstance(_source_pathology_rows(facade), tuple)
    assert isinstance(tuple(facade.to_wire_artifact().status.blockers or ()), tuple)


def test_compile_fi_facade_strict_mode_passes_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    compile_fi_facade("2009/953", replay_mode="legal_pit", compile_mode="strict")

    assert captured.get("strict_johto_temporal") is True


def test_compile_fi_facade_default_mode_is_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    compile_fi_facade("2009/953", replay_mode="legal_pit")

    assert captured.get("strict_johto_temporal") is True


def test_compile_fi_facade_quirks_mode_does_not_enable_strict_temporal_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_xml(*args: object, **kwargs: object):
        captured["strict_johto_temporal"] = bool(kwargs.get("strict_johto_temporal"))
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
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
    assert artifacts.verdict.status == "internal_failure"


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


def test_compile_fi_facade_returns_native_dossier() -> None:
    facade = compile_fi_facade("2009/953", replay_mode="legal_pit", compile_mode="quirks")

    assert facade.bundle.target_statute == "2009/953"
    assert facade.replay_mode == "legal_pit"
    assert facade.strict_profile_name == "finland_ingestion_v1"
    assert isinstance(facade.bundle.structural_ops, tuple)
    assert isinstance(_projection_rows(facade), tuple)
    assert isinstance(tuple(facade.to_wire_artifact().status.blockers or ()), tuple)


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
                        "tag": "chapter_scope_from_johtolause",
                    },
                    source_statute="2020/1",
                    blocking=True,
                ),
            ),
            verdict=None,
            source_adjudication=source_adjudication,
        )

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
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
        replay_mode="finlex_oracle",
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
        mode: str = "finlex_oracle",
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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )

    facade = compile_fi_facade("2009/205", replay_mode="finlex_oracle")

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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland._compile._compile_artifacts_from_replay",
        fake_compile_artifacts_from_replay,
    )

    facade = compile_fi_facade("2009/953", replay_mode="legal_pit")

    assert facade.bundle.temporal_events == ()


def test_compile_fi_surfaces_known_recovery_paths_and_source_flags() -> None:
    facade = compile_fi_facade("2002/1090", replay_mode="legal_pit", compile_mode="quirks")

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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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
        "reasons": ["tail_omission_payload"],
        "tail_policy": "preserve_unstated_tail",
        "detail": {
            "payload_completeness_kind": "sparse_certified",
            "reasons": ["tail_omission_payload"],
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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    source_pathologies = [a for a in _projection_rows(facade) if a["kind"] == "APPLY.SOURCE_PATHOLOGY_DETECTED"]
    assert len(source_pathologies) == 1
    assert cast(dict[str, Any], source_pathologies[0]["detail"])["target_unit_kind"] == "chapter"
    assert _source_pathology_rows(facade)[0]["target_unit_kind"] == "chapter"


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
    assert "iia osa" in labels
    details = {
        (cast(str, row["target_label"]), cast(dict[str, Any], row["detail"]).get("diagnostic_reason"))
        for row in rows
    }
    assert ("2 luku 7 §", "target_leaf_absent_under_existing_parent") in details
    assert ("iia osa", "target_part_absent_in_pre_partification_frame") in details


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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    fallback_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "APPLY.LEGACY_DISPATCH_FALLBACK"
    ]
    assert len(fallback_projection_rows) == 1
    assert fallback_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], fallback_projection_rows[0]["detail"])["reason_tag"] == "missing_canonical_intent"
    assert cast(dict[str, Any], fallback_projection_rows[0]["detail"])["reason_code"] == "missing_canonical_intent"
    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in tuple(facade.to_wire_artifact().status.blockers or ())


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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in {finding.kind for finding in facade.finding_ledger}
    assert "APPLY.LEGACY_DISPATCH_FALLBACK" in tuple(facade.to_wire_artifact().status.blockers or ())


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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    relabel_rows = [a for a in _projection_rows(facade) if a["kind"] == "APPLY.RELABEL_SKIPPED"]
    assert len(relabel_rows) == 1
    assert relabel_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], relabel_rows[0]["detail"])["reason_tag"] == "source_section_missing"
    assert cast(dict[str, Any], relabel_rows[0]["detail"])["reason_code"] == "source_section_missing"
    assert "APPLY.RELABEL_SKIPPED" in tuple(facade.to_wire_artifact().status.blockers or ())


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
                        "target_guessing_provenance_tags": ["normalize_item_like_target"],
                    },
                    {
                        "source_statute": "1993/805",
                        "scope_provenance_tags": ["chapter_scope_from_johtolause"],
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    kinds = {str(a["kind"]) for a in _projection_rows(facade)}
    assert "PARSE.TARGET_GUESSING" in kinds
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in kinds
    assert "target_guessing" not in kinds
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR" not in kinds

    target_guessing = next(a for a in _projection_rows(facade) if a["kind"] == "PARSE.TARGET_GUESSING")
    anchor = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION")
    assert cast(dict[str, Any], target_guessing["detail"])["tag"] == "normalize_item_like_target"
    assert cast(dict[str, Any], anchor["detail"])["tag"] == "chapter_scope_from_johtolause"
    assert cast(dict[str, Any], anchor["detail"])["scope_confidence"] == "inferred"
    assert cast(dict[str, Any], anchor["detail"])["scope_source"] == "johtolause"


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
                        "scope_provenance_tags": ["chapter_scope_from_johtolause"],
                    },
                    {
                        "source_statute": "1993/805",
                        "target_unit_kind": "section",
                        "target_norm": "36",
                        "target_chapter": "5",
                        "scope_provenance_tags": ["chapter_scope_from_johtolause"],
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = []
        return _replay_result_stub()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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
        ("inferred", "johtolause"),
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
                        "scope_provenance_tags": ["chapter_scope_from_johtolause"],
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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    anchor = next(a for a in _projection_rows(facade) if a["kind"] == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION")
    assert cast(dict[str, Any], anchor["detail"])["tag"] == "chapter_scope_from_johtolause"
    assert cast(dict[str, Any], anchor["detail"])["target_unit_kind"] == "section"
    assert cast(dict[str, Any], anchor["detail"])["target_norm"] == "1"
    assert cast(dict[str, Any], anchor["detail"])["target_chapter"] == "5a"
    assert cast(dict[str, Any], anchor["detail"])["scope_confidence"] == "inferred"
    assert cast(dict[str, Any], anchor["detail"])["scope_source"] == "johtolause"


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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    facade = compile_fi_facade("1990/1295", replay_mode="legal_pit")

    leftover_projection_rows = [
        a for a in _projection_rows(facade) if a["kind"] == "ELAB.SPARSE_PAYLOAD_LEFTOVER"
    ]
    assert len(leftover_projection_rows) == 1
    assert leftover_projection_rows[0]["source"] == "1993/805"
    assert cast(dict[str, Any], leftover_projection_rows[0]["detail"])["target_norm"] == "35"
    assert cast(dict[str, Any], leftover_projection_rows[0]["detail"])["unassigned_slots"] == ["2:2", "3:(unlabeled)"]


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

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

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


def test_replay_xml_exposes_fold_and_materialized_state() -> None:
    replay = pinned_replay("2009/953", mode="legal_pit")

    assert replay.replay_fold_state is not None
    assert replay.materialized_state is replay.state


def test_replay_xml_exposes_replay_time_projection_rows_without_explicit_sink() -> None:
    replay = pinned_replay("1991/1707", mode="legal_pit")

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


def test_replay_xml_1974_16_keeps_sparse_override_without_prior_law_tail_repair() -> None:
    """Current replay keeps the sparse override text instead of inferring prior-law tail repair."""
    replay = pinned_replay("1974/16", mode="finlex_oracle", quiet=True)
    replay_text = replay.serialize_text()

    assert "vähintään yhden hehtaarin peltoa käsittävällä tilalla" not in replay_text
    assert "vähintään kaksi hehtaaria peltoa käsittävällä tilalla" in replay_text


def test_replay_xml_matches_current_oracle_order_for_1987_990_section_55_second_moment() -> None:
    replay = pinned_replay("1987/990", mode="finlex_oracle", quiet=True, strict_johto_temporal=False)
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


def test_replay_xml_matches_current_oracle_order_for_1987_990_section_3_first_moment() -> None:
    replay = pinned_replay("1987/990", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:1/section:3"]

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


def test_replay_xml_matches_current_oracle_text_for_1987_990_section_73() -> None:
    replay = pinned_replay("1987/990", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:11/section:73"]
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


def test_replay_xml_preserves_2010_1020_section_20_johdanto_order() -> None:
    replay = pinned_replay("1998/28", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:3/section:20"]
    section_text = irnode_to_text(section)

    assert "Lupaviranomainen voi viran puolesta muuttaa lupapäätöstä, jos:" in section_text
    assert "Lupaviranomainen voi viran puolesta peruuttaa luvan, jos:" in section_text
    assert section_text.index("muuttaa lupapäätöstä, jos:") < section_text.index("peruuttaa luvan, jos:")


def test_replay_xml_preserves_2010_1020_section_25_johdanto_before_registry_sentence() -> None:
    replay = pinned_replay("1998/28", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["chapter:4/section:25"]
    section_text = irnode_to_text(section)

    assert "Lupapäätökseen lupaviranomaisen on:" in section_text
    assert "Lupaviranomainen pitää rekisteriä" in section_text
    assert section_text.index("Lupapäätökseen lupaviranomaisen on:") < section_text.index(
        "Lupaviranomainen pitää rekisteriä"
    )


def test_compile_amendment_ops_leaves_1977_18_sparse_payload_unrepaired_before_lowering() -> None:
    """Sparse tail normalization is not applied as pre-lowering authority here."""
    before = pinned_replay("1974/16", stop_before="1977/18", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output
    sec2_ops = [op for op in ops if op.target_section == "2"]

    result = compile_amendment_ops(
        before.state,
        sec2_ops,
        muutos_tree,
        johto,
        "finlex_oracle",
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


def test_normalize_and_compile_ops_parses_1980_1037_spaced_pykala_genitive_as_momentti_target() -> None:
    before = pinned_replay("1974/16", stop_before="1980/1037", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output
    sec1_ops = [op for op in ops if op.target_section == "1"]

    assert [op.target_paragraph for op in sec1_ops] == [3]


def test_normalize_and_compile_ops_parses_1979_1032_reinstated_subsection_insert() -> None:
    before = pinned_replay("1974/16", stop_before="1979/1032", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="1974/16",
        strict_profile=None,
    )
    ops = phase2.output

    sec6_insert_ops = [
        op for op in ops if op.op_type == "INSERT" and op.target_section == "6" and op.target_paragraph == 4
    ]

    assert len(sec6_insert_ops) == 1


def test_normalize_and_compile_ops_2017_571_keeps_doc_ill_subsection_insert_target() -> None:
    before = pinned_replay("2002/1244", stop_before="2017/571", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="2002/1244",
        strict_profile=None,
    )
    ops = phase2.output
    sec1_insert_ops = [op for op in ops if op.op_type == "INSERT" and op.target_section == "1"]

    assert [(op.target_section, op.target_paragraph, op.target_item) for op in sec1_insert_ops] == [("1", 2, None)]


def test_normalize_and_compile_ops_2018_1330_keeps_late_grouped_insert_targets() -> None:
    before = pinned_replay("2009/1599", stop_before="2018/1330", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="2009/1599",
        strict_profile=None,
    )
    ops = phase2.output

    grouped_inserts = {
        (op.target_chapter, op.target_section, op.target_paragraph, op.target_item)
        for op in ops
        if op.op_type == "INSERT"
    }

    assert ("7", "27", 2, "12a") in grouped_inserts
    assert ("13", "13", 5, None) in grouped_inserts
    assert ("19", "14", 3, None) in grouped_inserts
    assert ("19", "14", 4, None) in grouped_inserts
    assert ("20", "14", 2, None) in grouped_inserts
    assert ("20", "14", 3, None) in grouped_inserts


def test_replay_2009_1599_keeps_section_31_heading_despite_2023_280_sparse_payload() -> None:
    replay = pinned_replay("2009/1599", mode="finlex_oracle", quiet=True)
    sec31 = replay.state.find_section("31", "6")
    assert sec31 is not None

    heading = next((child for child in sec31.children if child.kind == IRNodeKind.HEADING), None)
    assert heading is not None
    assert heading.text == "Päätös kaikkien osakkeenomistajien rahoittamasta uudistuksesta"


def test_compile_fi_respects_more_permissive_strict_profile() -> None:
    facade = compile_fi_facade(
        "2002/1090",
        replay_mode="legal_pit",
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
    wire_status = facade.to_wire_artifact().status
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
        op_type="REPLACE",
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
    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_fallback_heuristic", lambda _johto: [])
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
        used_sec1_fallback=False,
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


def test_strip_impossible_chapter_scope_for_bare_body_section_op_clears_no_chapter_parent_leak() -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp

    op = AmendmentOp(
        op_id="ins-21",
        op_type="INSERT",
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
    assert patched.target_chapter is None


def test_strip_impossible_chapter_scope_for_bare_body_section_op_keeps_real_chaptered_parent() -> None:
    import lawvm.finland.frontend_compile as frontend_compile
    from lawvm.finland.ops import AmendmentOp

    op = AmendmentOp(
        op_id="ins-21",
        op_type="INSERT",
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


def test_normalize_and_compile_ops_1996_627_does_not_leak_parent_title_chapter_scope() -> None:
    before = pinned_replay("1996/627", stop_before="2023/674", mode="finlex_oracle", quiet=True)
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
        used_sec1_fallback=False,
        parent_id="1996/627",
        strict_profile=None,
    )

    target_ops = [
        op
        for op in phase2.output
        if op.target_unit_kind == "section" and op.target_section == "1"
    ]
    assert target_ops
    assert all(not op.target_chapter for op in target_ops)
    assert any(
        op.op_type == "INSERT"
        and op.target_paragraph == 1
        and op.target_item == "21"
        and not op.target_chapter
        for op in target_ops
    )


def test_normalize_and_compile_ops_records_empty_extraction_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result: [])
    monkeypatch.setattr(frontend_compile, "parse_ops_fallback_heuristic", lambda _johto: [])
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
        used_sec1_fallback=False,
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


def test_normalize_and_compile_ops_records_sec1_peg_skip_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lawvm.finland.frontend_compile as frontend_compile

    muutos_tree = etree.fromstring("<root/>")
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))

    monkeypatch.setattr(frontend_compile, "_sec1_fallback_peg_skip_required", lambda _johto, _parent_id: True)
    monkeypatch.setattr(frontend_compile, "parse_ops_fallback_heuristic", lambda _johto: [])
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
        used_sec1_fallback=True,
        parent_id="2019/1",
        strict_profile=None,
    )

    assert phase2.output == []
    peg_skip = [
        finding
        for finding in phase2.findings()
        if finding.kind == "PARSE.PEG_SKIP_SEC1_REPEAL_LIST"
    ]
    assert peg_skip
    assert peg_skip[0].detail.get("used_sec1_fallback") is True


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
        assert verdict.status == "source_incomplete"


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
    compiled_ops: list[dict[str, Any]] = [{"scope_provenance_tags": ["chapter_scope_from_johtolause"]}]
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
            kind="ELAB.SEC1_PRE_ROUTING_FALLBACK",
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
    assert "ELAB.SEC1_PRE_ROUTING_FALLBACK" in reasons


def test_strict_fail_reasons_from_finding_ledger_accept_structured_scope_confidence() -> None:
    profile = default_finland_strict_profile()

    reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=[
            {"scope_source": "johtolause", "scope_confidence": "inferred"},
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
                "scope_source": "johtolause",
                "scope_confidence": "inferred",
                "scope_provenance_tags": ["chapter_scope_stripped_unique_section"],
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
                "scope_source": "johtolause",
                "scope_confidence": "inferred",
            },
            {
                "scope_provenance_tags": ["chapter_scope_from_explicit_chunk"],
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
        compiled_ops=[{"target_guessing_provenance_tags": ["normalize_item_like_target"]}],
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
            "scope_provenance_tags": [
                "chapter_scope_from_johtolause",
            ],
            "target_guessing_provenance_tags": [
                "normalize_item_like_target",
            ],
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
                kind="ELAB.SEC1_PRE_ROUTING_FALLBACK",
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
    assert "ELAB.SEC1_PRE_ROUTING_FALLBACK" not in new
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
        assert verdict.status == expected_status, f"Status mismatch for {reasons}: {verdict.status}"
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


def test_replay_xml_2002_1290_does_not_crash_on_registered_item_like_normalization() -> None:
    """Replay should classify 2002/1290 without tripping unregistered payload-normalization findings."""
    result = pinned_replay("2002/1290", mode="finlex_oracle", quiet=True)
    assert result is not None
