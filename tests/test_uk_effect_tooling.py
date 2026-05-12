from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.uk_effect import (
    _collect_target_shape,
    _resolve_parent_presence,
    _resolve_target_presence,
    lowering_rejection_rule_counts,
)
from lawvm.tools.uk_effects import _EffectSummaryContext, summarize_uk_effect


class _FakeResolver:
    def __init__(self, mapping: dict[tuple[tuple[str, str], ...], str]) -> None:
        self.mapping = mapping

    def _derive_target_eid(self, target: LegalAddress) -> str:
        return self.mapping.get(target.path, "")


def test_resolve_target_presence_reports_base_and_oracle_hits() -> None:
    target = LegalAddress((("section", "68"), ("subsection", "7"), ("paragraph", "g")))
    resolver = _FakeResolver({target.path: "section-68-7-g"})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids={"section-68-7-g"},
        oracle_eids={"section-68-7-g", "section-68-7-ha"},
    )

    assert resolver_eid == "section-68-7-g"
    assert base_hit is True
    assert oracle_hit is True


def test_resolve_target_presence_handles_missing_resolver_match() -> None:
    target = LegalAddress((("section", "72"), ("subsection", "4"), ("paragraph", "ba")))
    resolver = _FakeResolver({})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids={"section-72-4"},
        oracle_eids={"section-72-4-a", "section-72-4-b"},
    )

    assert resolver_eid == ""
    assert base_hit is False
    assert oracle_hit is False


def test_resolve_parent_presence_reports_parent_hits() -> None:
    parent_eid, base_hit, oracle_hit = _resolve_parent_presence(
        "section-72-4-ba",
        base_eids={"section-72-4"},
        oracle_eids={"section-72-4", "section-72-4-c"},
    )

    assert parent_eid == "section-72-4"
    assert base_hit is True
    assert oracle_hit is True


def test_resolve_target_presence_matches_mixed_alphanumeric_case_insensitively() -> None:
    target = LegalAddress((("schedule", "7"), ("paragraph", "10"), ("subsection", "1a"), ("item", "a")))
    resolver = _FakeResolver({target.path: "schedule-7-paragraph-10-1a-a"})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids=set(),
        oracle_eids={"schedule-7-paragraph-10-1A-a"},
    )

    assert resolver_eid == "schedule-7-paragraph-10-1a-a"
    assert base_hit is False
    assert oracle_hit is True


def test_resolve_parent_presence_matches_mixed_alphanumeric_case_insensitively() -> None:
    parent_eid, base_hit, oracle_hit = _resolve_parent_presence(
        "schedule-7-paragraph-10-1a-a",
        base_eids=set(),
        oracle_eids={"schedule-7-paragraph-10-1A"},
    )

    assert parent_eid == "schedule-7-paragraph-10-1a"
    assert base_hit is False
    assert oracle_hit is True


def test_collect_target_shape_falls_back_to_text_map_and_descendant_hits() -> None:
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-28-1",
        text_map={"section-28-1": "1 Commissioners may inquire into the claim."},
        descendant_hit=True,
    )

    assert has_text is True
    assert has_children is True
    assert texts == ["1 Commissioners may inquire into the claim."]


def test_lowering_rejection_rule_counts_are_stable() -> None:
    assert lowering_rejection_rule_counts(
        [
            {"rule_id": "uk_effect_lowering_no_ops_rejected"},
            {"rule_id": "uk_effect_lowering_no_ops_rejected"},
            {"rule_id": "uk_effect_payload_missing"},
            {},
        ]
    ) == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
        "unknown": 1,
    }


def test_summarize_uk_effect_preserves_lowering_rejections(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-1",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    def fake_compile_effect_to_ir_ops(effect_arg, extracted, **kwargs):  # noqa: ANN001
        del effect_arg, extracted
        kwargs["lowering_rejections_out"].append(
            {
                "rule_id": "uk_effect_lowering_no_ops_rejected",
                "phase": "lowering",
                "effect_id": "eff-1",
            }
        )
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        fake_compile_effect_to_ir_ops,
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.n_ops == 0
    assert summary.lowering_rejections == (
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "phase": "lowering",
            "effect_id": "eff-1",
        },
    )
