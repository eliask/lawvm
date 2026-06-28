"""Render-fidelity tests for normalization-neutralized facet diffs in --dump.

The bench/event path compares facet text NORMALIZED (`_normalize_heading_for_diff`
for headings, `_normalize_wording_for_diff` otherwise). A facet pair that is
raw-unequal but normalization-equal produces NO event and does NOT count toward
err%. The aligned dump (`_render_aligned_node`) must render such pairs with a
distinct neutral marker (not the counted `+L`/`+F`) and must not let them force a
node's counted `~` marker.
"""
from __future__ import annotations

from lawvm.tools.structural_review import (
    _DIFF_MARK_CH,
    _DIFF_MARK_EQ,
    _DIFF_MARK_NEUTRAL,
    _DIFF_MARK_PL,
    _DIFF_MARK_PR,
    _render_aligned_node,
)


def _aligned_section(left_heading: str, right_heading: str) -> dict:
    """Build a minimal aligned section node with one differing heading facet."""
    return {
        "kind": "section",
        "left": {"kind": "section", "label": "30", "facets": {}, "children": []},
        "right": {"kind": "section", "label": "30", "facets": {}, "children": []},
        "facets": {
            "heading": {
                "left": {"text": left_heading},
                "right": {"text": right_heading},
            }
        },
        "children": [],
    }


def test_norm_equal_heading_pair_renders_neutral_not_counted() -> None:
    # Raw-unequal (oracle whitespace padding) but normalization-equal.
    aligned = _aligned_section("30 §          otsikko", "30 § otsikko")

    full = "\n".join(_render_aligned_node(aligned, indent=0, compact=False))

    # Neutral marker + tag present; counted side-markers absent.
    assert _DIFF_MARK_NEUTRAL in full
    assert "[norm-equal: presentation, not counted]" in full
    assert _DIFF_MARK_PL not in full
    assert _DIFF_MARK_PR not in full
    # Raw text still visible (auditor can see the artifact).
    assert "30 §          otsikko" in full
    # Node-level marker must NOT be the counted "changed" marker.
    assert full.startswith(_DIFF_MARK_EQ.strip()) or _DIFF_MARK_EQ in full.splitlines()[0]
    assert _DIFF_MARK_CH not in full.splitlines()[0]


def test_norm_equal_only_node_folds_away_in_compact() -> None:
    aligned = _aligned_section("30 §          otsikko", "30 § otsikko")

    compact = _render_aligned_node(aligned, indent=0, compact=True)

    # A section whose ONLY diff is neutralized must not appear in compact mode.
    assert compact == []


def test_genuine_heading_diff_still_counted() -> None:
    # Genuinely different (real text change), not collapsed by the normalizer.
    aligned = _aligned_section("30 § vanha otsikko", "30 § uusi otsikko")

    full_lines = _render_aligned_node(aligned, indent=0, compact=False)
    full = "\n".join(full_lines)

    assert _DIFF_MARK_PL in full
    assert _DIFF_MARK_PR in full
    assert _DIFF_MARK_NEUTRAL not in full
    # Node renders as counted "changed".
    assert _DIFF_MARK_CH in full_lines[0]

    # And it survives compact mode (it is a counted diff).
    compact = _render_aligned_node(aligned, indent=0, compact=True)
    assert compact != []


def test_norm_equal_wording_pair_renders_neutral() -> None:
    # Wording facet: dash-variant difference is normalization-neutral.
    aligned = {
        "kind": "subsection",
        "left": {"kind": "subsection", "label": "1", "facets": {}, "children": []},
        "right": {"kind": "subsection", "label": "1", "facets": {}, "children": []},
        "facets": {
            "wording": {
                "left": {"text": "ajalta 2012—2016"},
                "right": {"text": "ajalta 2012-2016"},
            }
        },
        "children": [],
    }

    full = "\n".join(_render_aligned_node(aligned, indent=0, compact=False))

    assert _DIFF_MARK_NEUTRAL in full
    assert _DIFF_MARK_PL not in full
    assert _DIFF_MARK_PR not in full


def test_parent_with_only_neutral_child_not_counted() -> None:
    # Parent section is facet-identical; its sole child has only a neutral diff.
    # The parent must NOT render as counted "~".
    aligned = {
        "kind": "section",
        "left": {"kind": "section", "label": "30", "facets": {}, "children": []},
        "right": {"kind": "section", "label": "30", "facets": {}, "children": []},
        "facets": {},
        "children": [
            {
                "kind": "subsection",
                "left": {"kind": "subsection", "label": "1", "facets": {}, "children": []},
                "right": {"kind": "subsection", "label": "1", "facets": {}, "children": []},
                "facets": {
                    "wording": {
                        "left": {"text": "ajalta 2012—2016"},
                        "right": {"text": "ajalta 2012-2016"},
                    }
                },
                "children": [],
            }
        ],
    }

    full_lines = _render_aligned_node(aligned, indent=0, compact=False)

    # Parent node line (first line) must not be marked counted-changed.
    assert _DIFF_MARK_CH not in full_lines[0]
    assert _DIFF_MARK_EQ in full_lines[0]

    # In compact mode the whole subtree folds away (nothing counted).
    compact = _render_aligned_node(aligned, indent=0, compact=True)
    assert compact == []
