"""End-to-end regression: item (kohta) RELABEL applies during Finland replay.

Amendment 1981/133 renumbers two items in Tavaramerkkiasetus (1964/296):
``section:9/subsection:1/item:4 -> item:9`` and
``chapter:4/section:22/subsection:1/item:6 -> item:22``. The lowering already
emits canonical ``Relabel`` intents for these; the apply layer previously
skipped item-kind relabels (``APPLY.RELABEL_SKIPPED`` /
``target_kind_unimplemented``), so the renumber silently failed.

These tests replay the real corpus to the boundary immediately after 1981/133
and assert the items now carry their new labels. Skipped when the corpus
archive is absent.
"""

from __future__ import annotations

import pytest

from lawvm.core.semantic_types import IRNodeKind


def _corpus_available() -> bool:
    """True when the finlex corpus archive resolves and is non-empty.

    Honors ``LAWVM_CANONICAL_DATA_ROOT`` / ``LAWVM_FARCHIVE_DB`` so the test runs
    in git worktrees that point at a shared corpus rather than carrying their own
    ``data/finlex.farchive``.
    """
    from lawvm.corpus_store import resolve_farchive_path

    try:
        path, _rule = resolve_farchive_path("finlex.farchive")
    except Exception:
        return False
    return path is not None and path.exists() and path.stat().st_size > 0


_corpus_skip = pytest.mark.skipif(
    not _corpus_available(),
    reason="finlex corpus archive not resolvable; skipping real-corpus item-relabel replay tests",
)


def _before_after(section: str) -> tuple[str, str]:
    from lawvm.tools.trace_section import build_trace_bundle

    bundle = build_trace_bundle(
        "1964/296",
        "1981/133",
        section,
        mode="legal_pit",
    )
    return bundle["before_text"], bundle["after_text"]


@_corpus_skip
def test_item_relabel_section_9_renumbers_4_to_9() -> None:
    before_text, after_text = _before_after("9 §")
    assert before_text != after_text
    # The renumbered item now carries a "9)" marker.
    assert "9)" in after_text


@_corpus_skip
def test_item_relabel_section_22_renumbers_6_to_22() -> None:
    before_text, after_text = _before_after("22 §")
    assert before_text != after_text
    assert "22)" in after_text


@_corpus_skip
def test_item_relabel_no_unhandled_relabel_finding() -> None:
    from lawvm.tools.replay_debug import build_replay_debug_bundle

    bundle = build_replay_debug_bundle(
        "1964/296",
        mode="legal_pit",
        source="1981/133",
        show_findings=True,
        show_failed_ops=True,
    )
    serialized = str(bundle).lower()
    # The item relabels must no longer be skipped as unimplemented.
    assert "target_kind_unimplemented" not in serialized
    assert "relabel target kind 'item'" not in serialized


@_corpus_skip
def test_item_relabel_applies_in_materialized_tree() -> None:
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
    from lawvm.tools.trace_section import _next_amendment_id

    next_source = _next_amendment_id("1964/296", "1981/133", "legal_pit")
    after_master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id="1964/296",
            mode="legal_pit",
            stop_before=next_source or "",
            quiet=True,
        ),
    )
    ir = after_master.materialized_state.ir

    def _item_labels(section_label: str, chapter_label: str) -> list[str]:
        for chapter in ir.children:
            if chapter.kind is not IRNodeKind.CHAPTER or chapter.label != chapter_label:
                continue
            for section in chapter.children:
                if section.kind is not IRNodeKind.SECTION or section.label != section_label:
                    continue
                labels: list[str] = []
                for sub in section.children:
                    if sub.kind is not IRNodeKind.SUBSECTION:
                        continue
                    labels.extend(
                        str(item.label)
                        for item in sub.children
                        if item.kind is IRNodeKind.PARAGRAPH and item.label
                    )
                return labels
        return []

    section_9_items = _item_labels("9", "2")
    assert "9" in section_9_items
    assert "4" not in section_9_items
