"""Tests for structural review dump rendering."""
from __future__ import annotations

import pytest

import lawvm.tools.structural_review as structural_review
from lawvm.tools.structural_review import _render_aligned_node
from lawvm.tools.structural_review import _selector_from_mode


def test_aligned_dump_uses_side_markers_recursively() -> None:
    aligned = {
        "kind": "section",
        "left": {
            "kind": "section",
            "label": "43",
            "facets": {
                "heading": {"text": "LawVM heading"},
            },
            "children": [
                {
                    "kind": "subsection",
                    "label": "1",
                    "facets": {
                        "wording": {"text": "Left body"},
                    },
                    "children": [
                        {
                            "kind": "item",
                            "label": "1",
                            "facets": {"wording": {"text": "Left item"}},
                            "children": [],
                        }
                    ],
                }
            ],
        },
        "right": {
            "kind": "section",
            "label": "43",
            "facets": {
                "heading": {"text": "Finlex heading"},
            },
            "children": [],
        },
        "facets": {
            "heading": {
                "left": {"text": "LawVM heading"},
                "right": {"text": "Finlex heading"},
            }
        },
        "children": [
            {
                "kind": "subsection",
                "left": {
                    "kind": "subsection",
                    "label": "1",
                    "facets": {"wording": {"text": "Left body"}},
                    "children": [
                        {
                            "kind": "item",
                            "label": "1",
                            "facets": {"wording": {"text": "Left item"}},
                            "children": [],
                        }
                    ],
                },
                "right": None,
                "facets": {},
                "children": [],
            }
        ],
    }

    lines = _render_aligned_node(aligned, indent=0, compact=False)
    text = "\n".join(lines)

    assert "+L" in text
    assert "+F" in text
    assert "LawVM:" not in text
    assert "Finlex:" not in text
    assert "  +L   1 mom." in text or "+L   1 mom." in text
    assert "  +L     1 kohta" in text or "+L     1 kohta" in text


def test_node_label_line_suppresses_opaque_synthetic_label() -> None:
    """_node_label_line must not emit an opaque synthetic label (__ord_N__) to display.

    When the dict has a synthetic opaque label and no visible_label,
    the renderer should return '(unlabeled)' for structural items
    rather than exposing the internal token.

    Provenance: corrigendum §1.2 — user-visible output must never carry synthetic labels.
    """
    from lawvm.tools.structural_review import _node_label_line

    # Opaque label — must NOT appear in output
    assert _node_label_line({"kind": "item", "label": "__ord_2__", "label_basis": "ordinal_fallback"}) == "(unlabeled)"
    assert _node_label_line({"kind": "subitem", "label": "__ord_3__", "label_basis": "ordinal_fallback"}) == "(unlabeled)"
    # Real label — must appear normally
    assert _node_label_line({"kind": "item", "label": "4", "label_basis": "ordinal_fallback"}) == "4 kohta"
    # No label — kind-only
    assert _node_label_line({"kind": "subsection", "label": "", "label_basis": "explicit"}) == "mom."


def test_selector_from_mode_is_explicit_and_fails_closed() -> None:
    bench_selector = _selector_from_mode("bench_comparable")
    latest_selector = _selector_from_mode("latest_cached_editorial")

    assert bench_selector.mode.value == "bench_comparable"
    assert latest_selector.mode.value == "latest_cached_editorial"

    with pytest.raises(ValueError, match="unsupported oracle selector mode"):
        _selector_from_mode("future_default")


def test_dump_statute_includes_event_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structural_review, "_get_statute_title", lambda statute_id, corpus: "Demo title")
    monkeypatch.setattr(
        structural_review,
        "compute_statute_section_diffs",
        lambda statute_id, corpus=None, mode="finlex_oracle", oracle_selector_mode="bench_comparable": (
            {
                "chapter:1/section:2": {
                    "semantic_diff": {
                        "kind": "updated",
                        "structural": 1,
                        "label": 0,
                        "text": 0,
                        "events": [
                            {
                                "kind": "ADD",
                                "semantic_path": ["section", "2"],
                                "match_basis": "structure",
                                "left_text": "old",
                                "right_text": "new",
                            }
                        ],
                    },
                    "compiler_observations": {
                        "kind": "ELAB.PAYLOAD_COMPLETENESS",
                        "count": 1,
                    },
                    "selected_claim_blocker": {
                        "kind": "claim_blocker",
                        "source": "2020/100",
                        "section": "1",
                    },
                    "aligned": {"kind": "section"},
                }
            },
            False,
        ),
    )
    monkeypatch.setattr(structural_review, "_render_aligned_node", lambda aligned, indent=0, compact=False: ["<aligned>"])

    dump = structural_review.dump_statute("2025/1349")

    assert "events (1):" in dump
    assert "ADD" in dump
    assert "section › 2" in dump
    assert "LawVM:  old" in dump
    assert "Finlex: new" in dump
    assert "context[section].compiler_observations: kind=ELAB.PAYLOAD_COMPLETENESS, count=1" in dump
    assert "context[section].selected_claim_blocker: kind=claim_blocker, source=2020/100, section=1" in dump


def test_review_sections_shows_section_context(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(structural_review, "_load_classifications", lambda: {})
    monkeypatch.setattr(
        structural_review,
        "_compute_live",
        lambda statute_filter, oracle_selector_mode="bench_comparable": {
            "statute_id": statute_filter,
            "sections": {
                "chapter:1/section:2": {
                    "semantic_diff": {
                        "kind": "updated",
                        "structural": 1,
                        "label": 0,
                        "text": 0,
                        "summary": "changed wording",
                        "events": [
                            {
                                "kind": "ADD",
                                "semantic_path": ["section", "2"],
                                "match_basis": "structure",
                                "left_text": "old",
                                "right_text": "new",
                            }
                        ],
                    },
                    "compiler_observations": {
                        "kind": "ELAB.PAYLOAD_COMPLETENESS",
                        "count": 1,
                    },
                    "selected_claim_blocker": {
                        "kind": "claim_blocker",
                        "source": "2020/100",
                        "section": "1",
                    },
                }
            },
        },
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "k")

    structural_review.review_sections(statute_filter="2025/1349", unreviewed_only=False)
    out = capsys.readouterr().out

    assert "summary: changed wording" in out
    assert "context[section].compiler_observations: kind=ELAB.PAYLOAD_COMPLETENESS, count=1" in out
    assert "context[section].selected_claim_blocker: kind=claim_blocker, source=2020/100, section=1" in out
