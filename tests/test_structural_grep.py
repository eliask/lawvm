"""Tests for structural_grep filter logic.

Tests the filter matching logic using mock section data — does NOT require
a live corpus or farchive.
"""
from __future__ import annotations

from lawvm.tools.structural_grep import (
    GrepMatch,
    StructuralGrepFilter,
    _build_match,
    _collect_text,
    _extract_node_text,
    _matches_filter,
    _node_label_basis,
)


# ---------------------------------------------------------------------------
# Fixtures: mock section data
# ---------------------------------------------------------------------------


def _section(
    *,
    replay: dict | None = None,
    oracle: dict | None = None,
    diff_kind: str = "identical",
    events: list[dict] | None = None,
) -> dict:
    """Build a minimal mock section data dict as produced by build_semantic_support."""
    sd: dict = {
        "kind": diff_kind,
        "summary": "",
        "structural": 0,
        "label": 0,
        "text": 0,
        "editorial": 0,
        "events": events or [],
    }
    result: dict = {"semantic_diff": sd}
    if replay is not None:
        result["replay"] = replay
    if oracle is not None:
        result["oracle"] = oracle
    return result


def _node(
    kind: str = "section",
    label: str = "1",
    text: str = "",
    label_basis: str = "explicit",
    children: list[dict] | None = None,
    facets: dict | None = None,
) -> dict:
    """Build a minimal SemanticStructureNode dict."""
    n: dict = {"kind": kind, "label": label}
    if text:
        n["text"] = text
    if label_basis != "explicit":
        n["label_basis"] = label_basis
    if children:
        n["children"] = children
    if facets:
        n["facets"] = facets
    return n


# ---------------------------------------------------------------------------
# Tests: _collect_text
# ---------------------------------------------------------------------------


class TestCollectText:
    def test_plain_text(self) -> None:
        parts: list[str] = []
        _collect_text({"text": "hello world"}, parts)
        assert parts == ["hello world"]

    def test_nested_children(self) -> None:
        parts: list[str] = []
        node = {
            "text": "parent",
            "children": [
                {"text": "child1"},
                {"text": "child2", "children": [{"text": "grandchild"}]},
            ],
        }
        _collect_text(node, parts)
        assert parts == ["parent", "child1", "child2", "grandchild"]

    def test_facets(self) -> None:
        parts: list[str] = []
        node = {
            "text": "",
            "facets": {
                "heading": {"text": "Otsikko"},
                "wording": {"text": "Sanamuoto"},
            },
        }
        _collect_text(node, parts)
        assert "Otsikko" in parts
        assert "Sanamuoto" in parts


# ---------------------------------------------------------------------------
# Tests: _node_label_basis
# ---------------------------------------------------------------------------


class TestNodeLabelBasis:
    def test_explicit_default(self) -> None:
        sec = _section(replay=_node())
        assert _node_label_basis(sec, "replay") == "explicit"

    def test_custom_basis(self) -> None:
        sec = _section(oracle=_node(label_basis="editorial_repeal_notice"))
        assert _node_label_basis(sec, "oracle") == "editorial_repeal_notice"

    def test_missing_side(self) -> None:
        sec = _section(replay=_node())
        assert _node_label_basis(sec, "oracle") == ""


# ---------------------------------------------------------------------------
# Tests: _matches_filter
# ---------------------------------------------------------------------------


class TestMatchesFilter:
    def test_empty_filter_matches_anything(self) -> None:
        # Empty filter should still match (returns True for any section with diff)
        # but is_empty() is True — main() rejects it before calling _matches_filter
        filt = StructuralGrepFilter()
        sec = _section(replay=_node(), oracle=_node())
        assert _matches_filter("1 §", sec, filt) is True

    def test_diff_kind_match(self) -> None:
        filt = StructuralGrepFilter(diff_kind=["text_only"])
        sec = _section(replay=_node(), oracle=_node(), diff_kind="text_only")
        assert _matches_filter("1 §", sec, filt) is True

    def test_diff_kind_no_match(self) -> None:
        filt = StructuralGrepFilter(diff_kind=["text_only"])
        sec = _section(replay=_node(), oracle=_node(), diff_kind="identical")
        assert _matches_filter("1 §", sec, filt) is False

    def test_not_diff_kind(self) -> None:
        filt = StructuralGrepFilter(not_diff_kind=["editorial_only", "identical"])
        sec_ed = _section(replay=_node(), oracle=_node(), diff_kind="editorial_only")
        sec_text = _section(replay=_node(), oracle=_node(), diff_kind="text_only")
        assert _matches_filter("1 §", sec_ed, filt) is False
        assert _matches_filter("1 §", sec_text, filt) is True

    def test_replay_label_basis(self) -> None:
        filt = StructuralGrepFilter(replay_label_basis=["repeal_placeholder"])
        sec = _section(
            replay=_node(label_basis="repeal_placeholder"),
            oracle=_node(),
        )
        assert _matches_filter("1 §", sec, filt) is True

    def test_oracle_label_basis(self) -> None:
        filt = StructuralGrepFilter(oracle_label_basis=["editorial_repeal_notice"])
        sec = _section(
            replay=_node(),
            oracle=_node(label_basis="editorial_repeal_notice"),
        )
        assert _matches_filter("1 §", sec, filt) is True

    def test_not_oracle_label_basis(self) -> None:
        filt = StructuralGrepFilter(not_oracle_label_basis=["editorial_repeal_notice"])
        sec = _section(
            replay=_node(),
            oracle=_node(label_basis="editorial_repeal_notice"),
        )
        assert _matches_filter("1 §", sec, filt) is False

    def test_replay_missing(self) -> None:
        filt = StructuralGrepFilter(replay_missing=True)
        sec_missing = _section(oracle=_node(text="kumottu"))
        sec_present = _section(replay=_node(), oracle=_node())
        assert _matches_filter("1 §", sec_missing, filt) is True
        assert _matches_filter("1 §", sec_present, filt) is False

    def test_oracle_missing(self) -> None:
        filt = StructuralGrepFilter(oracle_missing=True)
        sec = _section(replay=_node(text="content"))
        assert _matches_filter("1 §", sec, filt) is True

    def test_oracle_text_matches(self) -> None:
        filt = StructuralGrepFilter(oracle_text_matches=r"kumottu|on kumottu")
        sec_match = _section(
            replay=_node(),
            oracle=_node(text="Tämä pykälä on kumottu."),
        )
        sec_no = _section(
            replay=_node(),
            oracle=_node(text="Jokin muu teksti."),
        )
        assert _matches_filter("1 §", sec_match, filt) is True
        assert _matches_filter("1 §", sec_no, filt) is False

    def test_replay_text_matches(self) -> None:
        filt = StructuralGrepFilter(replay_text_matches=r"\d+ §")
        sec = _section(
            replay=_node(text="Tämä 5 § viittaus"),
            oracle=_node(),
        )
        assert _matches_filter("1 §", sec, filt) is True

    def test_oracle_text_not_matches(self) -> None:
        filt = StructuralGrepFilter(oracle_text_not_matches=r"kumottu")
        sec = _section(
            replay=_node(),
            oracle=_node(text="On kumottu"),
        )
        assert _matches_filter("1 §", sec, filt) is False

    def test_replay_text_not_matches(self) -> None:
        filt = StructuralGrepFilter(replay_text_not_matches=r"foo")
        sec = _section(
            replay=_node(text="bar baz"),
            oracle=_node(),
        )
        assert _matches_filter("1 §", sec, filt) is True

    def test_has_children(self) -> None:
        filt = StructuralGrepFilter(has_children=True)
        sec_with = _section(
            replay=_node(children=[_node(kind="subsection")]),
            oracle=_node(),
        )
        sec_without = _section(replay=_node(), oracle=_node())
        assert _matches_filter("1 §", sec_with, filt) is True
        assert _matches_filter("1 §", sec_without, filt) is False

    def test_no_children(self) -> None:
        filt = StructuralGrepFilter(has_children=False)
        sec = _section(replay=_node(), oracle=_node())
        assert _matches_filter("1 §", sec, filt) is True

    def test_diff_event(self) -> None:
        filt = StructuralGrepFilter(diff_event=["editorial_repeal_notice"])
        events = [{"kind": "editorial_repeal_notice"}]
        sec = _section(replay=_node(), oracle=_node(), events=events)
        assert _matches_filter("1 §", sec, filt) is True

    def test_diff_event_no_match(self) -> None:
        filt = StructuralGrepFilter(diff_event=["unit_missing_left"])
        events = [{"kind": "editorial_repeal_notice"}]
        sec = _section(replay=_node(), oracle=_node(), events=events)
        assert _matches_filter("1 §", sec, filt) is False

    def test_op_filter(self) -> None:
        filt = StructuralGrepFilter(has_op=["REPEAL"])
        sec = _section(replay=_node(), oracle=_node())
        ops: set[str] = {"REPEAL", "REPLACE"}
        assert _matches_filter("1 §", sec, filt, ops) is True

    def test_no_op_filter(self) -> None:
        filt = StructuralGrepFilter(no_op=["REPEAL"])
        sec = _section(replay=_node(), oracle=_node())
        ops: set[str] = {"REPEAL"}
        assert _matches_filter("1 §", sec, filt, ops) is False

    def test_combined_filters(self) -> None:
        """Multiple filters AND together."""
        filt = StructuralGrepFilter(
            oracle_text_matches=r"kumottu",
            not_diff_kind=["editorial_only", "identical"],
        )
        # kumottu text + text_only diff → matches both
        sec = _section(
            replay=_node(text="content"),
            oracle=_node(text="kumottu"),
            diff_kind="text_only",
        )
        assert _matches_filter("1 §", sec, filt) is True
        # kumottu text + editorial_only diff → fails not_diff_kind
        sec2 = _section(
            replay=_node(),
            oracle=_node(text="kumottu"),
            diff_kind="editorial_only",
        )
        assert _matches_filter("1 §", sec2, filt) is False

    def test_no_semantic_diff(self) -> None:
        """Section without semantic_diff returns False."""
        filt = StructuralGrepFilter(diff_kind=["text_only"])
        sec: dict = {"replay": _node(), "oracle": _node()}
        assert _matches_filter("1 §", sec, filt) is False


# ---------------------------------------------------------------------------
# Tests: _build_match
# ---------------------------------------------------------------------------


class TestBuildMatch:
    def test_basic(self) -> None:
        sec = _section(
            replay=_node(text="replay text", label_basis="explicit"),
            oracle=_node(text="oracle text", label_basis="editorial_repeal_notice"),
            diff_kind="text_only",
            events=[{"kind": "wording_text_changed"}],
        )
        m = _build_match("2006/1299", "1 §", sec)
        assert m.statute_id == "2006/1299"
        assert m.section_key == "1 §"
        assert m.diff_kind == "text_only"
        assert m.oracle_label_basis == "editorial_repeal_notice"
        assert m.replay_label_basis == "explicit"
        assert "replay text" in m.replay_text
        assert "oracle text" in m.oracle_text
        assert m.events == ["wording_text_changed"]


# ---------------------------------------------------------------------------
# Tests: GrepMatch output
# ---------------------------------------------------------------------------


class TestGrepMatchOutput:
    def test_one_line(self) -> None:
        m = GrepMatch(
            statute_id="2006/1299",
            section_key="1 §",
            diff_kind="text_only",
            oracle_label_basis="explicit",
            replay_label_basis="explicit",
        )
        line = m.one_line()
        assert "2006/1299" in line
        assert "1 §" in line
        assert "text_only" in line

    def test_verbose_line(self) -> None:
        m = GrepMatch(
            statute_id="2006/1299",
            section_key="1 §",
            diff_kind="text_only",
            oracle_label_basis="explicit",
            replay_label_basis="explicit",
            oracle_text="oracle content here",
            replay_text="replay content here",
            events=["wording_text_changed"],
        )
        v = m.verbose_line()
        assert "oracle:" in v
        assert "replay:" in v
        assert "events:" in v

    def test_to_dict(self) -> None:
        m = GrepMatch(
            statute_id="2006/1299",
            section_key="1 §",
            diff_kind="text_only",
            oracle_label_basis="explicit",
            replay_label_basis="explicit",
        )
        d = m.to_dict()
        assert d["statute_id"] == "2006/1299"
        assert d["diff_kind"] == "text_only"


# ---------------------------------------------------------------------------
# Tests: StructuralGrepFilter
# ---------------------------------------------------------------------------


class TestFilterProperties:
    def test_is_empty(self) -> None:
        assert StructuralGrepFilter().is_empty() is True

    def test_not_empty_with_text_filter(self) -> None:
        f = StructuralGrepFilter(oracle_text_matches="foo")
        assert f.is_empty() is False

    def test_not_empty_with_diff_kind(self) -> None:
        f = StructuralGrepFilter(diff_kind=["text_only"])
        assert f.is_empty() is False

    def test_needs_ops(self) -> None:
        assert StructuralGrepFilter().needs_ops() is False
        assert StructuralGrepFilter(has_op=["REPEAL"]).needs_ops() is True
        assert StructuralGrepFilter(no_op=["INSERT"]).needs_ops() is True


# ---------------------------------------------------------------------------
# Tests: _extract_node_text
# ---------------------------------------------------------------------------


class TestExtractNodeText:
    def test_missing_side(self) -> None:
        sec = _section(replay=_node(text="hello"))
        assert _extract_node_text(sec, "oracle") == ""

    def test_flat_text(self) -> None:
        sec = _section(oracle=_node(text="hello world"))
        assert "hello world" in _extract_node_text(sec, "oracle")

    def test_deep_text(self) -> None:
        sec = _section(replay=_node(
            text="parent",
            children=[_node(text="child")],
        ))
        text = _extract_node_text(sec, "replay")
        assert "parent" in text
        assert "child" in text
