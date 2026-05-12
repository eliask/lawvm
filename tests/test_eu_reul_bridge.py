"""REUL bridge smoke tests."""
from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.reul_bridge import _parse_celex, REULBridge


def test_map_celex_to_uk_eid_normalizes_known_kinds() -> None:
    bridge = REULBridge()
    eid = bridge.map_celex_to_uk_eid("32016R0679", "art/1/para/2")

    assert eid == "eur_2016_679_article_1_paragraph_2"


def test_map_celex_to_uk_eid_is_lenient_on_invalid_celex() -> None:
    bridge = REULBridge()
    assert (
        bridge.map_celex_to_uk_eid("bad-celex", "article/1")
        == "eur_unknown_unknown_bad-celex"
    )


def test_map_celex_to_uk_eid_normalizes_case_and_separators() -> None:
    bridge = REULBridge()
    assert (
        bridge.map_celex_to_uk_eid("32016r0679", "art_1.para_2")
        == "eur_2016_679_article_1_paragraph_2"
    )
    assert (
        bridge.map_celex_to_uk_eid("32016R0679", "Article/1/POINT/2")
        == "eur_2016_679_article_1_item_2"
    )


def test_map_celex_to_uk_eid_handles_mixed_separators_and_trailing_text() -> None:
    bridge = REULBridge()

    assert (
        bridge.map_celex_to_uk_eid("32016R0679", "article / 1 . point .2// ")
        == "eur_2016_679_article_1_item_2"
    )


def test_map_celex_to_uk_eid_accepts_whitespace_and_common_aliases() -> None:
    bridge = REULBridge()

    assert (
        bridge.map_celex_to_uk_eid(" 32016r0679 ", " sec/1/par/2 ")
        == "eur_2016_679_section_1_paragraph_2"
    )


def test_map_celex_to_uk_eid_with_empty_path_returns_celex_prefix() -> None:
    bridge = REULBridge()

    assert bridge.map_celex_to_uk_eid("32016R0679", "   ") == "eur_2016_679"


def test_parse_celex_accepts_case_insensitive_kinds() -> None:
    parsed = _parse_celex(" 32016r0679 ")
    assert parsed is not None
    assert parsed.year == "2016"
    assert parsed.number == "679"


def test_parse_celex_rejects_non_regulatory_celex_kind() -> None:
    assert _parse_celex("32000X1234") is None


def test_resolve_retained_law_uri_nested_path() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1",
                    children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="nested point"),),
                ),),
        ),
    )
    bridge = REULBridge()

    node = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/article/1/point/1",
        statute,
    )

    assert node is not None
    assert node.text == "nested point"


def test_resolve_retained_law_uri_path_aliases() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1",
                    children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.ITEM, label="2", text="alias point"),)),
                        IRNode(kind=IRNodeKind.ITEM, label="2", text="alias point direct"),),
                ),),
        ),
    )
    bridge = REULBridge()

    node = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/ARTICLE/1/PAR/1/POINT/2",
        statute,
    )
    assert node is not None
    assert node.text == "alias point"

    point = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/article/1/point/2",
        statute,
    )
    assert point is not None
    assert point.text == "alias point direct"

    point_with_tail = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/article/1/point/2/",
        statute,
    )
    assert point_with_tail is not None
    assert point_with_tail.text == "alias point direct"


def test_resolve_retained_law_uri_with_query_and_fragments() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1",
                    children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="paragraph two"),),
                ),),
        ),
    )
    bridge = REULBridge()

    node = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/article/1/para/2?lang=fi#x",
        statute,
    )
    assert node is not None
    assert node.text == "paragraph two"


def test_resolve_retained_law_uri_mismatch_celex_returns_none() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)),
    )
    bridge = REULBridge()

    assert (
        bridge.resolve_retained_law_uri(
            "retained-law://celex/99999R0000/article/1",
            statute,
        )
        is None
    )


def test_resolve_retained_law_uri_records_failure_reason() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)),
    )
    bridge = REULBridge()
    diagnostics: list[dict[str, Any]] = []

    assert (
        bridge.resolve_retained_law_uri(
            "retained-law://celex/32016R0679/article/99",
            statute,
            diagnostics_out=diagnostics,
        )
        is None
    )

    assert diagnostics == [
        {
            "rule_id": "eu_reul_uri_resolution_failed",
            "kind": "eu_reul_uri_resolution_failed",
            "family": "target_resolution_recovery",
            "phase": "lowering",
            "reason": "EU REUL bridge could not resolve retained-law URI against the EU statute tree",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "uri": "retained-law://celex/32016R0679/article/99",
            "statute_id": "32016R0679",
            "detail": {
                "reason_code": "target_unresolved",
                "path": [("section", "99")],
            },
        }
    ]


def test_resolve_retained_law_uri_records_malformed_path_reason() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)),
    )
    bridge = REULBridge()
    diagnostics: list[dict[str, Any]] = []

    assert (
        bridge.resolve_retained_law_uri(
            "retained-law://celex/32016R0679/article",
            statute,
            diagnostics_out=diagnostics,
        )
        is None
    )

    assert diagnostics[0]["rule_id"] == "eu_reul_uri_resolution_failed"
    assert diagnostics[0]["family"] == "target_resolution_recovery"
    assert diagnostics[0]["phase"] == "lowering"
    assert diagnostics[0]["strict_disposition"] == "block"
    assert diagnostics[0]["detail"]["reason_code"] == "too_few_parts"


def test_resolve_retained_law_uri_does_not_record_diagnostic_on_success() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="ok"),)),
    )
    bridge = REULBridge()
    diagnostics: list[dict[str, Any]] = []

    node = bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679/article/1",
        statute,
        diagnostics_out=diagnostics,
    )

    assert node is not None
    assert node.text == "ok"
    assert diagnostics == []


def test_resolve_retained_law_uri_with_non_celex_host_returns_none() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)),
    )
    bridge = REULBridge()

    assert (
        bridge.resolve_retained_law_uri(
            "retained-law://law/32016R0679/article/1",
            statute,
        )
        is None
    )


def test_resolve_retained_law_uri_with_celex_query_suffix() -> None:
    statute = IRStatute(
        statute_id="32016R0679",
        title="Sample",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
        ),
    )
    bridge = REULBridge()

    assert bridge.resolve_retained_law_uri(
        "retained-law://celex/32016R0679?view=full/article/1",
        statute,
    ) is not None
