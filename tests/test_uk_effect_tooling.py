from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.uk_effect import (
    _collect_target_shape,
    _resolve_parent_presence,
    _resolve_target_presence,
)


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
