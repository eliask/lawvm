"""Source-faithfulness sensor for UK whole-provision repeals."""
from __future__ import annotations

from types import SimpleNamespace

from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.repeal_source_warrant import (
    REPEAL_SOURCE_WARRANT_RULE_ID,
    collect_repeal_source_warrant_observations,
    repeal_op_target_in_source,
)


def _op(action, target, raw_text, *, op_id="op1", statute_id="ukpga/2006/46"):
    return SimpleNamespace(
        action=action,
        target=target,
        op_id=op_id,
        source=SimpleNamespace(raw_text=raw_text, statute_id=statute_id),
    )


class TestRepealOpTargetInSource:
    def test_whole_section_named_in_source_is_warranted(self) -> None:
        op = _op(StructuralAction.REPEAL, "section:15A", "Theft Act 1968 Sections 15, 15A, 15B and 16.")
        assert repeal_op_target_in_source(op) is True

    def test_whole_section_absent_from_source_is_unwarranted(self) -> None:
        # the over-generation case: 652 not named, only the range/header is
        op = _op(StructuralAction.REPEAL, "section:652", "SCHEDULE 16 Repeals Sections 651 to 719")
        assert repeal_op_target_in_source(op) is False

    def test_whole_schedule_warrant(self) -> None:
        assert repeal_op_target_in_source(
            _op(StructuralAction.REPEAL, "schedule:4", "Schedules 4, 5 and 6 are repealed")
        ) is True
        assert repeal_op_target_in_source(
            _op(StructuralAction.REPEAL, "schedule:9", "Schedules 4, 5 and 6 are repealed")
        ) is False

    def test_sub_provision_repeal_is_out_of_scope(self) -> None:
        op = _op(StructuralAction.REPEAL, "section:10/subsection:2", "a omit “, 9”")
        assert repeal_op_target_in_source(op) is None

    def test_non_repeal_action_is_out_of_scope(self) -> None:
        op = _op(StructuralAction.INSERT, "section:23A", "after section 23 insert 23A ...")
        assert repeal_op_target_in_source(op) is None

    def test_letter_suffix_label_matched_case_insensitively(self) -> None:
        assert repeal_op_target_in_source(
            _op(StructuralAction.REPEAL, "section:430F", "Sections 1 to 430F repealed; section 430f noted")
        ) is True


class TestCollectObservations:
    def test_emits_only_for_unwarranted(self) -> None:
        ops = [
            _op(StructuralAction.REPEAL, "section:15A", "Sections 15, 15A, 15B and 16."),  # warranted
            _op(StructuralAction.REPEAL, "section:652", "SCHEDULE 16 Sections 651 to 719", op_id="bad"),  # unwarranted
            _op(StructuralAction.INSERT, "section:23A", "insert 23A"),  # out of scope
            _op(StructuralAction.REPEAL, "section:10/subsection:2", "omit words"),  # out of scope
        ]
        obs = collect_repeal_source_warrant_observations(ops)
        assert len(obs) == 1
        row = obs[0]
        assert row["rule_id"] == REPEAL_SOURCE_WARRANT_RULE_ID
        assert row["blocking"] is False
        assert row["target"] == "section:652"
        assert row["op_id"] == "bad"

    def test_clean_op_set_emits_nothing(self) -> None:
        ops = [_op(StructuralAction.REPEAL, "section:9", "sections 9, 13, 16, 18 and 19 are repealed")]
        assert collect_repeal_source_warrant_observations(ops) == []
