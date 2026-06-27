from __future__ import annotations

import datetime as dt
from typing import Any, cast

from lxml import etree
import pytest

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.phase_result import Finding
import lawvm.finland.process_route_rejection as route_rejection_mod
from lawvm.finland.process_route_rejection import (
    RouteRejectionBranch,
    ProcessRouteRejectionContext,
    classify_route_rejection,
)
from lawvm.finland.ops import OpType, AmendmentOp
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest
from lawvm.finland.metadata import CommencementExpiryOverride
from lawvm.finland.source_model import AmendmentSourceModel


def _route_context(
    route_reason: str,
    *,
    target_amendment_id: str = "",
    source_title: str = "",
    parent_title: str = "",
    johto: str = "",
) -> tuple[ProcessRouteRejectionContext, list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    prints: list[str] = []

    def record_finding(**kwargs: Any) -> Finding:
        findings.append(kwargs)
        return Finding(
            kind=kwargs["kind"],
            role=kwargs["role"],
            stage="process_muutoslaki.route_rejection",
            detail=kwargs["detail"],
            source_statute=kwargs["source_statute"],
            blocking=kwargs["role"] == "obligation",
        )

    ctx = ProcessRouteRejectionContext(
        amendment_id="2020/100",
        parent_id="2021/100",
        parent_title=parent_title,
        source_title=source_title,
        johto=johto,
        source_model=AmendmentSourceModel.from_tree(etree.Element("Laki")),
        route_reason=route_reason,
        route_target_amendment_id=target_amendment_id,
        strict_profile=None,
        replay_mode="full",
        lo_ops_out=None,
        vts_skipped_targets=[],
        commencement_expiry_override_notes=[],
        effect_relation_signals=[],
        record_finding=record_finding,
        replay_print=prints.append,
    )
    return ctx, findings, prints


def _recorded_detail(route_reason: str, **kwargs: Any) -> dict[str, Any]:
    ctx, findings, _prints = _route_context(route_reason, **kwargs)
    ctx._record_source_incomplete()
    assert len(findings) == 1
    assert findings[0]["kind"] == "APPLY.SOURCE_INCOMPLETE"
    assert findings[0]["role"] == "obligation"
    return findings[0]["detail"]


def test_route_rejection_num_collision_has_stable_rule_metadata() -> None:
    detail = _recorded_detail("num_collision_skip")

    assert detail["route_reason"] == "num_collision_skip"
    assert detail["rule_id"] == "fi.route_rejection.num_collision"
    assert detail["family"] == "source_routing"
    assert detail["phase"] == "process_muutoslaki.route_rejection"
    assert detail["branch"] == "num_collision"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"


def test_route_rejection_pending_amendment_carries_target_and_rule_metadata() -> None:
    ctx, findings, _prints = _route_context(
        "pending_amendment_of_parent_skip",
        target_amendment_id="2019/50",
    )
    ctx._record_source_incomplete()

    assert [finding["kind"] for finding in findings] == [
        "APPLY.SOURCE_INCOMPLETE",
        "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
    ]
    assert all(finding["role"] == "obligation" for finding in findings)
    detail = findings[0]["detail"]

    assert detail["route_reason"] == "pending_amendment_of_parent_skip"
    assert detail["rule_id"] == "fi.route_rejection.pending_amendment_of_parent"
    assert detail["branch"] == "pending_amendment_of_parent"
    assert detail["target_amendment_id"] == "2019/50"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"
    assert findings[1]["detail"]["target_amendment_id"] == "2019/50"
    assert len(ctx.effect_relation_signals) == 1
    signal = ctx.effect_relation_signals[0]
    assert signal.signal_kind == "pending_amendment"
    assert signal.relation_kind == "modifies_effect"
    assert signal.source_statute == "2020/100"
    assert signal.target_statute == "2019/50"
    assert signal.target_resolution == "target_instrument_unresolved"


def test_route_rejection_delegated_authority_has_stable_rule_metadata() -> None:
    detail = _recorded_detail("delegated_authority_nojalla_skip")

    assert detail["route_reason"] == "delegated_authority_nojalla_skip"
    assert detail["rule_id"] == "fi.route_rejection.delegated_authority_nojalla"
    assert detail["branch"] == "delegated_authority_nojalla"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"


def test_route_rejection_citation_mismatch_has_stable_rule_metadata() -> None:
    detail = _recorded_detail("citation_mismatch_skip")

    assert detail["route_reason"] == "citation_mismatch_skip"
    assert detail["rule_id"] == "fi.route_rejection.citation_mismatch"
    assert detail["branch"] == "citation_mismatch"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"


def test_route_rejection_meta_repeal_has_stable_rule_metadata() -> None:
    ctx, findings, _prints = _route_context(
        "citation_mismatch_skip",
        johto="kumotaan eräiden lakien muuttamisesta annetun lain ( 123/2010 ) 3 §",
    )
    ctx._record_source_incomplete()

    assert [finding["kind"] for finding in findings] == [
        "APPLY.META_REPEAL_EFFECT_RECORDED",
        "APPLY.SOURCE_INCOMPLETE",
    ]
    detail = findings[1]["detail"]

    assert detail["route_reason"] == "citation_mismatch_skip"
    assert detail["rule_id"] == "fi.route_rejection.meta_repeal"
    assert detail["branch"] == "meta_repeal"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"
    assert findings[0]["detail"]["target_amendment_id"] == "2010/123"
    assert findings[0]["role"] == "observation"
    assert len(ctx.effect_relation_signals) == 1
    signal = ctx.effect_relation_signals[0]
    assert signal.signal_kind == "meta_repeal"
    assert signal.relation_kind == "repeals_effect"
    assert signal.source_statute == "2020/100"
    assert signal.target_statute == "2010/123"
    assert signal.target_resolution == "target_instrument_resolved"


def test_route_rejection_title_meta_repeal_does_not_emit_vts_parent_repeals() -> None:
    class SourceModelWithVtsRepeal:
        def commencement_expiry_override(self, _amendment_id: str) -> None:
            return None

        def extract_vts_cross_statute_repeals(self, **_kwargs: Any) -> list[AmendmentOp]:
            return [
                AmendmentOp(
                    op_id="phantom_parent_repeal",
                    op_type=OpType.REPEAL,
                    target_section="4a",
                    target_unit_kind="section",
                    voimaantulo_repeal=True,
                )
            ]

    source_title = "Laki rikosrekisterilain 4 a ja 6 §:n muuttamisesta annetun lain kumoamisesta"
    ctx, findings, _prints = _route_context(
        "citation_mismatch_skip",
        source_title=source_title,
        parent_title="Rikosrekisterilaki",
        johto="Eduskunnan päätöksen mukaisesti säädetään:",
    )
    ctx.source_model = cast(AmendmentSourceModel, SourceModelWithVtsRepeal())

    result = ctx.handle()

    assert result.ops == ()
    assert result.should_return_state is True
    assert result.vts_ops_enrich_done is False
    assert result.skip_to_compile is False
    assert [finding["kind"] for finding in findings] == [
        "APPLY.META_REPEAL_EFFECT_UNRESOLVED",
        "APPLY.SOURCE_INCOMPLETE",
    ]
    assert findings[1]["detail"]["rule_id"] == "fi.route_rejection.meta_repeal"
    assert findings[1]["detail"]["branch"] == "meta_repeal"


def test_route_rejection_title_meta_repeal_resolves_target_instrument_from_title() -> None:
    ctx, findings, _prints = _route_context(
        "citation_mismatch_skip",
        source_title=(
            "Laki rikosrekisterilain 4 a ja 6 §:n muuttamisesta "
            "annetun lain kumoamisesta (27/2012)"
        ),
        parent_title="Rikosrekisterilaki",
        johto="Eduskunnan päätöksen mukaisesti säädetään:",
    )

    ctx.handle()

    assert findings[0]["kind"] == "APPLY.META_REPEAL_EFFECT_RECORDED"
    assert findings[0]["detail"]["target_amendment_id"] == "2012/27"
    assert len(ctx.effect_relation_signals) == 1
    assert ctx.effect_relation_signals[0].target_statute == "2012/27"


def test_replay_1993_770_title_meta_repeal_does_not_repeal_parent_section_4a() -> None:
    replay = replay_xml(
        request=ReplayXmlRequest(
            parent_id="1993/770",
            mode="legal_pit",
            stop_before="2014/149",
            quiet=True,
        )
    )

    section_4a = replay.find_section("4a")

    assert section_4a is not None
    section_text = " ".join(irnode_to_text(section_4a).split())
    assert "passia, Suomen kansalaisuutta" in section_text
    assert "lapsen huostaanottoa tai adoptiota" in section_text


def test_skipped_amendment_expiry_override_records_lifecycle_when_rewrite_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SourceModel:
        def commencement_expiry_override(
            self,
            amendment_id: str,
        ) -> CommencementExpiryOverride:
            assert amendment_id == "2020/100"
            return CommencementExpiryOverride(
                target_mid="2019/50",
                labels=frozenset({"4 a"}),
                expiry=dt.date(2022, 12, 31),
            )

    ctx, _findings, _prints = _route_context("citation_mismatch_skip")
    ctx.source_model = cast(Any, SourceModel())
    monkeypatch.setattr(route_rejection_mod, "_rewrite_lo_op_source_expiry", lambda *args, **kwargs: False)

    ctx._apply_skipped_amendment_expiry_override()

    assert len(ctx.commencement_expiry_override_notes) == 1
    note = ctx.commencement_expiry_override_notes[0]
    assert note.source_statute == "2020/100"
    assert note.target_statute == "2019/50"
    assert note.scope.kind == "section"
    assert note.scope.labels == ("4a",)
    assert note.expiry == "2022-12-31"


def test_route_rejection_title_targets_other_statute_has_stable_rule_metadata() -> None:
    detail = _recorded_detail(
        "citation_mismatch_skip",
        source_title="Asetus eroraha-asetuksen muuttamisesta annetun asetuksen muuttamisesta",
        parent_title="Työttömyysturvalaki",
    )

    assert detail["route_reason"] == "citation_mismatch_skip"
    assert detail["rule_id"] == "fi.route_rejection.title_targets_other_statute"
    assert detail["branch"] == "title_targets_other_statute"
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "skip_with_finding"


def test_classify_route_rejection_returns_typed_branch() -> None:
    disposition = classify_route_rejection(
        route_reason="citation_mismatch_skip",
        johto="",
        source_title="",
        parent_title="",
    )

    assert disposition.branch is RouteRejectionBranch.CITATION_MISMATCH
    assert disposition.as_detail()["branch"] == "citation_mismatch"
