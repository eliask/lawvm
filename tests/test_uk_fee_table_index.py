"""Regression: the fee-table index builder must tolerate non-element nodes.

``source_root.iter()`` yields comment and processing-instruction nodes whose
``tag`` is a callable, not a string.  ``_uk_build_fee_table_index`` previously
called ``el.tag.split(...)`` unconditionally and raised ``AttributeError`` on
such a node.  Because the fee-target refinement loop only catches ``ValueError``
(AGENTS.md §1.10), that error escaped and aborted replay of any statute whose
source XML contained a comment near a table (witnessed on ukpga/1978/30).
"""

from __future__ import annotations

from lawvm.uk_legislation import table_sources as TS

ET = TS.ET


def _root(xml: str):  # type: ignore[no-untyped-def]
    return ET.fromstring(xml)


class TestFeeTableIndexToleratesComments:
    def test_comment_node_does_not_crash(self) -> None:
        root = _root(
            "<root><!-- editorial note -->"
            "<table><tr><td>Enactment specifying fees</td><td>Fee payable</td></tr>"
            "<tr><td>s. 1</td><td>5</td></tr></table></root>"
        )
        entries = TS._uk_build_fee_table_index(root)
        assert len(entries) == 1
        assert entries[0].rows[0].col0 == "Enactment specifying fees"

    def test_processing_instruction_does_not_crash(self) -> None:
        root = _root(
            "<root><?display mode='compact'?>"
            "<table><tr><td>fee payable</td></tr></table></root>"
        )
        # Must not raise; the PI node is skipped, the fee table is found.
        assert len(TS._uk_build_fee_table_index(root)) == 1

    def test_non_fee_table_with_comment_skipped(self) -> None:
        root = _root(
            "<root><!-- c --><table><tr><td>something else</td></tr></table></root>"
        )
        assert TS._uk_build_fee_table_index(root) == ()
